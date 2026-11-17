"""The stdio MCP server stops its in-process renderer itself (gh #63).

It used to leave that to ``atexit``, which runs after the interpreter has shut its thread
pools down: the app's shutdown could no longer hand ``renderer.close`` to a thread, raised,
printed a traceback into every MCP client's log and skipped the checkpointer's close.

The real thing (a subprocess, Chromium, a client that goes away) is in
tests/e2e/test_mcp_stdio_shutdown_63.py.
"""
from __future__ import annotations

import signal

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from langstage_codraft import cli, render, standalone
from langstage_codraft import server as server_mod
from langstage_codraft import store as store_mod
from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.server import create_app
from langstage_codraft.store import Store


# ------------------------------------------------------------ the app's shutdown
class _Renderer:
    def __init__(self, fail: bool = False):
        self.closed = 0
        self.fail = fail

    def close(self) -> None:
        self.closed += 1
        if self.fail:
            raise OSError("the browser is already gone")


class _Saver:
    """Stands where the app keeps its checkpointer's context manager."""

    def __init__(self, inner):
        self.inner = inner
        self.closed = 0

    async def __aexit__(self, *exc):
        self.closed += 1
        if self.inner is not None:
            return await self.inner.__aexit__(*exc)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    store = Store(tmp_path / "data" / "codraft.db")
    yield create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []},
                     store=store)
    render.set_renderer(None)


def _swap(app, fake: _Renderer) -> tuple[_Saver, render.Renderer]:
    """Inside a running app: put ``fake`` where the renderer is, and watch the saver."""
    st = app.state.codraft
    saver = st.saver_cm = _Saver(st.saver_cm)
    real = render.get_renderer()
    render.set_renderer(fake)
    return saver, real


def test_shutdown_closes_the_checkpointer_when_the_renderer_close_raises(app):
    fake = _Renderer(fail=True)
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        saver, real = _swap(app, fake)
    real.close()
    assert fake.closed == 1  # tried once, not retried
    assert saver.closed == 1, "a renderer that fails to close must not skip the checkpointer"
    assert render.get_renderer() is None


def test_shutdown_closes_the_renderer_itself_when_no_thread_pool_is_left(app, monkeypatch):
    """What an interpreter that is exiting gives the app: nothing can be handed to a thread."""
    fake = _Renderer()

    async def no_pool(*_a, **_k):
        raise RuntimeError("cannot schedule new futures after interpreter shutdown")

    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        saver, real = _swap(app, fake)
        monkeypatch.setattr(server_mod.asyncio, "to_thread", no_pool)
    monkeypatch.undo()
    real.close()
    assert fake.closed == 1 and saver.closed == 1
    assert render.get_renderer() is None


def test_a_normal_shutdown_closes_the_renderer_once(app):
    fake = _Renderer()
    with TestClient(app):
        saver, real = _swap(app, fake)
    real.close()
    assert fake.closed == 1 and saver.closed == 1


# ------------------------------------------------------------- the lazy renderer
class _App:
    """A Standalone that starts nothing."""

    made: list[_App] = []

    def __init__(self, **_kw):
        self.closed = 0
        self.fail = False
        _App.made.append(self)

    def start(self):
        return self

    def renderer(self):
        return self

    def close(self) -> None:
        self.closed += 1
        if self.fail:
            raise RuntimeError("cannot schedule new futures after interpreter shutdown")


@pytest.fixture
def lazy(store, monkeypatch):
    _App.made = []
    registered: list = []
    monkeypatch.setattr(standalone, "Standalone", _App)
    monkeypatch.setattr(standalone.atexit, "register", registered.append)
    monkeypatch.setattr(standalone, "_lazy", None)
    monkeypatch.setattr(standalone, "_lazy_at_exit", False)
    cfg = CodraftConfig.resolve(overrides={"data_dir": store.path.parent}, use_toml=False, env={})
    return standalone.lazy_renderer(cfg), registered


def test_close_lazy_renderer_stops_the_app_once(lazy):
    factory, _ = lazy
    standalone.close_lazy_renderer()  # nothing started: nothing to do
    assert _App.made == []
    assert factory() is factory()
    (app,) = _App.made
    standalone.close_lazy_renderer()
    standalone.close_lazy_renderer()  # what atexit does afterwards
    assert app.closed == 1 and standalone._lazy is None


def test_close_lazy_renderer_never_raises(lazy):
    factory, _ = lazy
    factory().fail = True
    standalone.close_lazy_renderer()
    assert _App.made[0].closed == 1 and standalone._lazy is None


def test_the_exit_hook_is_the_safe_close_and_is_registered_once(lazy):
    factory, registered = lazy
    assert registered == []  # building the factory starts and registers nothing
    factory()
    standalone.close_lazy_renderer()
    factory()  # a later render starts a fresh app
    assert len(_App.made) == 2 and _App.made[1].closed == 0
    assert registered == [standalone.close_lazy_renderer]
    standalone.close_lazy_renderer()


# ------------------------------------------------------------ `langstage-codraft mcp`
@pytest.fixture
def mcp_command(tmp_path, monkeypatch):
    """Run `mcp` in this process with a server that ends the way the test says."""
    from langstage_codraft import mcp_server

    monkeypatch.chdir(tmp_path)
    for k in ("LANGSTAGE_CODRAFT_DATA_DIR", "LANGSTAGE_CODRAFT_PORT"):
        monkeypatch.delenv(k, raising=False)
    closed: list[str] = []
    seen: dict = {}

    def run(ending):
        class Server:
            def run(self, transport):
                seen["transport"] = transport
                seen["factory"] = render._FACTORY
                seen["handlers"] = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
                return ending(seen)

        monkeypatch.setattr(mcp_server, "build_mcp_server", lambda *a, **k: Server())
        monkeypatch.setattr(standalone, "close_lazy_renderer", lambda: closed.append("closed"))
        before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        res = CliRunner().invoke(cli.main, ["mcp", "--data-dir", str(tmp_path / "data"), "--port", "1"])
        after = {s: signal.getsignal(s) for s in before}
        assert after == before, "the command left its signal handlers installed"
        return res

    yield run, closed, seen
    render.set_renderer_factory(None)
    render.set_renderer(None)
    opened = store_mod._STORE
    store_mod.set_store(None)
    if opened is not None:
        opened.close()


def test_mcp_closes_the_renderer_when_the_client_goes_away(mcp_command):
    run, closed, seen = mcp_command
    res = run(lambda _seen: None)  # stdin closed: the server's run() returns
    assert res.exit_code == 0, res.output
    assert seen["transport"] == "stdio" and seen["factory"] is not None  # no server: in-process
    assert closed == ["closed"]


def test_mcp_closes_the_renderer_when_the_server_fails(mcp_command):
    run, closed, _ = mcp_command

    def ending(_seen):
        raise RuntimeError("the transport broke")

    res = run(ending)
    assert isinstance(res.exception, RuntimeError) and closed == ["closed"]


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_mcp_closes_the_renderer_on_a_signal_then_lets_the_signal_end_it(mcp_command, monkeypatch, signum):
    """SIGTERM and Ctrl+C: stop the renderer first, then die of the signal as before. The
    signal can't be left to unwind the server, whose stdin read would hold the exit up."""
    import os

    run, closed, _ = mcp_command
    steps: list = []

    class Gone(Exception):
        """Stands for the process dying in os.kill."""

    def kill(pid, sig):
        # By now the handlers are the defaults, so the signal (or a second, impatient one)
        # ends the process instead of coming back here.
        steps.append(("kill", pid, sig, list(closed),
                      [signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)]))
        raise Gone

    monkeypatch.setattr(os, "kill", kill)

    def ending(seen):
        handler = seen["handlers"][signum]
        assert handler is seen["handlers"][signal.SIGTERM] and callable(handler)
        handler(signum, None)  # what the interpreter calls when the signal lands

    res = run(ending)
    assert isinstance(res.exception, Gone)
    assert steps == [("kill", os.getpid(), signum, ["closed"], [signal.SIG_DFL, signal.SIG_DFL])]


def test_signal_handlers_are_skipped_off_the_main_thread():
    import threading

    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    out: list = []
    t = threading.Thread(target=lambda: out.append(cli._close_on_signal(lambda: None)))
    t.start()
    t.join()
    assert {s: signal.getsignal(s) for s in before} == before  # nothing was installed
    out[0]()  # and the restore has nothing to put back
    assert {s: signal.getsignal(s) for s in before} == before


# ------------------------------------------------------------------ Renderer.close
def _renderer(tmp_path) -> render.Renderer:
    return render.Renderer("http://127.0.0.1:1", tmp_path / "renders")


def _wait_stopped(r: render.Renderer, seconds: float = 10) -> bool:
    r._thread.join(seconds)
    return not r._thread.is_alive()


def test_renderer_close_stops_its_loop_and_can_be_called_twice(tmp_path):
    r = _renderer(tmp_path)
    r.close()
    assert _wait_stopped(r)
    r.close()  # nothing to wait for the second time


def test_renderer_close_that_runs_out_of_time_finishes_before_the_loop_stops(tmp_path, monkeypatch):
    """A slow browser close is left to finish; the loop stops after it, never under it."""
    import asyncio
    import concurrent.futures
    import threading

    r = _renderer(tmp_path)
    release = threading.Event()
    order: list[str] = []

    class SlowBrowser:
        async def close(self):
            while not release.is_set():
                await asyncio.sleep(0.01)
            order.append("browser closed")

    r._browser = SlowBrowser()
    real_result = concurrent.futures.Future.result
    monkeypatch.setattr(concurrent.futures.Future, "result",
                        lambda self, timeout=None: real_result(self, 0.2))  # close()'s 15 s, shortened
    r.close()
    monkeypatch.undo()
    assert r._thread.is_alive() and order == [], "the loop was stopped under an unfinished close"
    release.set()
    assert _wait_stopped(r) and order == ["browser closed"]
