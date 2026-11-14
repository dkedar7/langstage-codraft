"""Rendering without a running server (gh #44): the in-process app is private to the
process, and `export` / the stdio MCP server reach for it when there's no server.

The renders themselves need Chromium and live in tests/e2e/test_standalone_44.py.
"""
from __future__ import annotations

import socket

import httpx
import pytest
from click.testing import CliRunner

from langstage_codraft import cli, render, standalone
from langstage_codraft import store as store_mod
from langstage_codraft.config import CodraftConfig
from langstage_codraft.standalone import ExportError, NoData, Standalone
from langstage_codraft.store import Store


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    s = Store(d / "codraft.db")
    doc = s.create_document("Quarterly report")
    s.close()
    return d, doc["id"]


def _cfg(d, **over) -> CodraftConfig:
    return CodraftConfig.resolve(overrides={"data_dir": d, **over}, use_toml=False, env={})


# ----------------------------------------------------------- the private server
def test_binds_loopback_only_on_a_port_the_os_picked(data_dir):
    d, _ = data_dir
    with Standalone(cfg=_cfg(d, host="0.0.0.0", port=8060)) as sa:
        host, port = sa._sock.getsockname()
        assert host == "127.0.0.1"  # never the configured bind address
        assert port not in (0, 8060) and sa.base_url == f"http://127.0.0.1:{port}"
        assert sa.cfg.host == "127.0.0.1"


def test_every_request_needs_the_one_off_password(data_dir):
    d, doc_id = data_dir
    with Standalone(cfg=_cfg(d)) as sa:
        # Loopback is not exempt: another local process can't read or edit through it.
        for path in ("/api/health", "/api/docs", f"/api/docs/{doc_id}", f"/print/{doc_id}"):
            assert httpx.get(sa.base_url + path).status_code == 401, path
        assert httpx.post(sa.base_url + "/api/docs", json={"title": "x"}).status_code == 401
        assert httpx.get(sa.base_url + "/api/health", headers=sa.headers).status_code == 200
        got = httpx.get(f"{sa.base_url}/api/docs/{doc_id}", headers=sa.headers).json()
        assert got["title"] == "Quarterly report"
        first = sa.cfg.auth_password
    with Standalone(cfg=_cfg(d)) as again:
        assert len(first) >= 32 and again.cfg.auth_password != first  # made per run


def test_a_configured_password_or_proxy_secret_is_not_what_guards_it(data_dir):
    d, _ = data_dir
    cfg = _cfg(d, auth_password="house-password", proxy_secret="proxy-secret", renderer_url="http://proxy.example.org")
    with Standalone(cfg=cfg) as sa:
        assert sa.cfg.auth_password not in ("", "house-password") and sa.cfg.proxy_secret == ""
        assert sa.cfg.renderer_url == ""
        r = render.get_renderer()
        assert r is not None and r.base_url == sa.base_url  # it renders from itself, not the proxy
        assert r.headers == sa.headers
        house = render.auth_headers(cfg)
        assert httpx.get(sa.base_url + "/api/health", headers=house).status_code == 401
    # The caller's config is left as it was.
    assert (cfg.auth_password, cfg.proxy_secret, cfg.port) == ("house-password", "proxy-secret", 8060)


def test_it_has_no_mcp_endpoint_and_answers_only_loopback_names(data_dir):
    d, _ = data_dir
    with Standalone(cfg=_cfg(d)) as sa:
        r = httpx.post(sa.base_url + "/mcp", headers=sa.headers, json={})
        assert r.status_code in (404, 405)
        rebound = httpx.get(sa.base_url + "/api/health", headers={**sa.headers, "host": "evil.example"})
        assert rebound.status_code == 403


def test_close_stops_listening_and_puts_the_store_back(data_dir):
    d, _ = data_dir
    from langstage_codraft import themes

    before, saved_before = store_mod._STORE, themes._saved
    sa = Standalone(cfg=_cfg(d)).start()
    assert themes._saved is not saved_before
    port = sa._sock.getsockname()[1]
    assert store_mod._STORE not in (None, before) and render.get_renderer() is not None
    sa.close()
    sa.close()  # twice is fine
    assert store_mod._STORE is before and render.get_renderer() is None
    assert themes._saved is saved_before  # saved themes aren't read from the store it closed
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()


def test_a_store_the_process_already_has_is_reused_and_kept(data_dir, store):
    # `store` (conftest) is the process's open store, as in the stdio MCP server.
    doc = store.create_document("Already open")
    with Standalone(cfg=_cfg(store.path.parent), store=store) as sa:
        assert store_mod._STORE is store
        assert httpx.get(f"{sa.base_url}/api/docs/{doc['id']}", headers=sa.headers).status_code == 200
    assert store_mod._STORE is store
    assert store.get_document(doc["id"])["title"] == "Already open"  # still open


def test_starting_writes_nothing_to_stdout(data_dir, capfd):
    """stdout is the protocol channel when this runs inside the stdio MCP server."""
    d, _ = data_dir
    with Standalone(cfg=_cfg(d)) as sa:
        httpx.get(sa.base_url + "/api/health", headers=sa.headers)
        httpx.get(sa.base_url + "/api/health")
    assert capfd.readouterr().out == ""


def test_an_empty_directory_is_refused_and_left_alone(tmp_path):
    missing = tmp_path / "nothing-here"
    with pytest.raises(NoData, match=r"no codraft\.db"):
        Standalone(missing).start()
    assert not missing.exists()  # no stray .codraft directory or database
    with pytest.raises(NoData):
        standalone.export_document(missing, "doc_x")


def test_export_reports_an_unknown_document(data_dir):
    d, _ = data_dir
    with Standalone(cfg=_cfg(d)) as sa, pytest.raises(ExportError) as err:
        sa.export("doc_nope", "pdf")
    assert err.value.status == 404 and "doc_nope" in err.value.detail


def test_public_names():
    import langstage_codraft

    assert langstage_codraft.export_document is standalone.export_document
    assert langstage_codraft.Standalone is Standalone
    assert langstage_codraft.ExportError is ExportError


# ------------------------------------------------------------------------- CLI
class FakeStandalone:
    """Stands in for the in-process app: records what was asked, renders nothing."""

    instances: list = []
    titles = {"doc_a": "report", "doc_b": "report", "doc_c": "paper"}

    def __init__(self, data_dir=None, *, cfg=None, store=None):
        self.cfg, self.calls, self.closed = cfg, [], False
        FakeStandalone.instances.append(self)

    def start(self):
        return self

    def close(self):
        self.closed = True

    def get(self, doc_id, fmt="pdf"):
        self.calls.append((doc_id, fmt))
        if doc_id not in self.titles:
            return httpx.Response(404, json={"detail": f"document {doc_id} not found"})
        stem = self.titles[doc_id]
        if fmt in ("png", "html"):
            return httpx.Response(200, content=b"zip", headers={
                "content-type": "application/zip", "content-disposition": f'attachment; filename="{stem}-{fmt}.zip"'})
        return httpx.Response(200, content=fmt.encode(), headers={
            "content-type": "application/octet-stream", "content-disposition": f'attachment; filename="{stem}.{fmt}"'})


@pytest.fixture
def fake(monkeypatch):
    FakeStandalone.instances = []
    monkeypatch.setattr(standalone, "Standalone", FakeStandalone)

    def no_server(url, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", no_server)
    return FakeStandalone


def test_data_dir_renders_in_process_without_asking_a_server(fake, tmp_path):
    out = tmp_path / "deep" / "out.pdf"
    res = CliRunner().invoke(cli.main, ["export", "doc_a", "-o", str(out), "--data-dir", str(tmp_path / "d")])
    assert res.exit_code == 0, res.output
    sa = fake.instances[0]
    assert sa.calls == [("doc_a", "pdf")] and sa.closed
    assert sa.cfg.data_dir == tmp_path / "d"
    assert out.read_bytes() == b"pdf"  # and the missing output folder was made
    assert "No server" not in res.output


def test_several_documents_and_formats_share_one_renderer(fake, tmp_path):
    outdir = tmp_path / "out"
    res = CliRunner().invoke(cli.main, ["export", "doc_a", "doc_b", "doc_c", "-f", "pdf", "-f", "png",
                                        "-o", str(outdir), "--standalone"])
    assert res.exit_code == 0, res.output
    assert len(fake.instances) == 1 and fake.instances[0].closed
    assert fake.instances[0].calls == [(d, f) for d in ("doc_a", "doc_b", "doc_c") for f in ("pdf", "png")]
    # doc_a and doc_b share a title: the second keeps its own file.
    assert sorted(p.name for p in outdir.iterdir()) == [
        "doc_b-report-png.zip", "doc_b-report.pdf", "paper-png.zip", "paper.pdf", "report-png.zip", "report.pdf"]


def test_one_missing_document_fails_the_run_but_not_the_others(fake, tmp_path):
    res = CliRunner().invoke(cli.main, ["export", "doc_nope", "doc_c", "-o", str(tmp_path), "--standalone"])
    assert res.exit_code == 1
    assert "doc_nope: not found in" in res.output and (tmp_path / "paper.pdf").is_file()
    assert fake.instances[0].closed


def test_no_flags_and_no_server_falls_back_to_in_process(fake, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(cli.main, ["export", "doc_c", "-f", "png"])
    assert res.exit_code == 0, res.output
    assert "No server at http://localhost:8060; rendering in-process" in res.output
    assert (tmp_path / "paper-png.zip").read_bytes() == b"zip"


def test_no_flags_and_a_running_server_exports_through_it(monkeypatch, tmp_path):
    seen = []

    def live(url, params=None, headers=None, timeout=None):
        seen.append(url)
        return httpx.Response(200, content=b"x", headers={"content-type": "application/pdf"})

    monkeypatch.setattr(httpx, "get", live)
    monkeypatch.setattr(standalone, "Standalone", None)  # must not be touched
    res = CliRunner().invoke(cli.main, ["export", "doc_1", "-o", str(tmp_path / "o.pdf")])
    assert res.exit_code == 0, res.output
    assert seen == ["http://localhost:8060/api/health", "http://localhost:8060/api/docs/doc_1/export"]


def test_port_or_url_never_falls_back(fake, tmp_path):
    res = CliRunner().invoke(cli.main, ["export", "doc_a", "-p", "1"])
    assert res.exit_code == 1 and "no Codraft server at http://localhost:1" in res.output
    assert "--data-dir" in res.output and fake.instances == []


@pytest.mark.parametrize("flags", [["--data-dir", "d", "-p", "8060"], ["--standalone", "--url", "http://x"]])
def test_in_process_and_live_flags_dont_mix(flags):
    res = CliRunner().invoke(cli.main, ["export", "doc_a", *flags])
    assert res.exit_code == 2 and "one or the other" in res.output


def test_several_exports_need_a_directory(fake, tmp_path):
    f = tmp_path / "out.pdf"
    f.write_bytes(b"")
    res = CliRunner().invoke(cli.main, ["export", "doc_a", "doc_c", "-o", str(f), "--standalone"])
    assert res.exit_code == 2 and "must be a directory" in res.output


def test_cli_explains_a_directory_with_no_documents(tmp_path):
    res = CliRunner().invoke(cli.main, ["export", "doc_a", "--data-dir", str(tmp_path / "void")])
    assert res.exit_code == 1 and "no Codraft documents in" in res.output
    assert not (tmp_path / "void").exists()


# -------------------------------------------------------- lazy renderer (MCP)
@pytest.fixture
def no_factory():
    yield
    render.set_renderer_factory(None)
    standalone._lazy = None


def _text(result) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, tuple):
        content = content[0]
    return "\n".join(getattr(c, "text", "") for c in content)


async def test_mcp_view_pages_starts_the_renderer_on_first_use(store, no_factory, tmp_path):
    from langstage_codraft.mcp_server import build_mcp_server

    shot = tmp_path / "p1.jpg"
    shot.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 32)
    started = []

    class Fake:
        async def screenshot_pages(self, doc_id, pages=None, max_pages=4):
            return [{"page": 1, "path": str(shot), "total": 1}]

        def call(self, coro):
            import asyncio

            return asyncio.new_event_loop().run_until_complete(coro)

    def factory():
        started.append(1)
        return Fake()

    server = build_mcp_server()
    doc = store.create_document("x")
    # Nothing is started until the tool is called, and not for a document that doesn't exist.
    render.set_renderer_factory(factory)
    assert _text(await server.call_tool("view_pages", {"doc_id": "doc_nope"})).startswith("ERROR:")
    assert started == []
    res = await server.call_tool("view_pages", {"doc_id": doc["id"]})
    content = res.content if hasattr(res, "content") else res[0]
    assert started == [1] and any(getattr(c, "type", "") == "image" for c in content)


async def test_mcp_view_pages_says_why_the_renderer_didnt_start(store, no_factory):
    from langstage_codraft.mcp_server import build_mcp_server

    def factory():
        raise RuntimeError("the in-process Codraft renderer failed to start")

    render.set_renderer_factory(factory)
    doc = store.create_document("x")
    out = _text(await build_mcp_server().call_tool("view_pages", {"doc_id": doc["id"]}))
    assert out.startswith("ERROR:") and "failed to start" in out


def test_lazy_renderer_starts_one_app_over_the_open_store(store, no_factory):
    factory = standalone.lazy_renderer(_cfg(store.path.parent))
    assert standalone._lazy is None  # building the factory starts nothing
    try:
        r1, r2 = factory(), factory()
        sa = standalone._lazy
        assert r1 is r2 and r1 is render.get_renderer() and r1.base_url == sa.base_url
        assert store_mod._STORE is store
    finally:
        standalone._lazy.close()
