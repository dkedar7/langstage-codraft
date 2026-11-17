"""`langstage-codraft mcp` exits quietly after it rendered something (gh #63).

An MCP client ends a session by closing the server's stdin. After `view_pages` had started
the in-process renderer, that used to print a traceback ending "cannot schedule new futures
after interpreter shutdown" and "Application shutdown failed", which MCP clients show in
their server logs. This runs the issue's own steps against a real subprocess and Chromium.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

import pytest

from langstage_codraft.store import Store


@pytest.fixture(scope="module", autouse=True)
def _chromium():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            p.chromium.launch().close()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium not available: {exc}")


def _env() -> dict:
    env = dict(os.environ)
    for k in [k for k in env if k.startswith("LANGSTAGE_")]:
        del env[k]
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@pytest.fixture
def session(tmp_path):
    """A stdio MCP server on a fresh data directory, after one `view_pages` call."""
    d = tmp_path / ".codraft"
    s = Store(d / "codraft.db")
    doc = s.create_document("Memo")
    s.insert_block(doc["id"], "text", {"markdown": "One line, one page."})
    s.close()
    p = subprocess.Popen(
        [sys.executable, "-m", "langstage_codraft.cli", "mcp", "--data-dir", str(d), "--port", "1"],
        cwd=tmp_path, env=_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")

    def send(msg: dict) -> None:
        p.stdin.write(json.dumps(msg) + "\n")
        p.stdin.flush()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "t", "version": "1"}}})
        p.stdout.readline()
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "view_pages", "arguments": {"doc_id": doc["id"]}}})
        content = json.loads(p.stdout.readline())["result"]["content"]
        assert [c["type"] for c in content] == ["text", "image"], content  # the renderer is up
        yield p
    finally:
        if p.poll() is None:
            p.kill()
        p.wait(30)
        for f in (p.stdin, p.stdout, p.stderr):
            try:
                f.close()
            except OSError:
                pass


def _quiet(err: str) -> None:
    for sign in ("Traceback", "shutdown failed", "RuntimeError", "interpreter shutdown"):
        assert sign not in err, err[-2000:]


def test_stdio_mcp_exits_quietly_when_the_client_disconnects(session):
    session.stdin.close()  # the client goes away, as an MCP client does at session end
    assert session.wait(90) == 0
    _quiet(session.stderr.read())


@pytest.mark.skipif(os.name == "nt", reason="Windows can't deliver these signals to a child: it kills outright")
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_stdio_mcp_stops_at_once_and_quietly_on_a_signal(session, signum):
    """What a client sends when it isn't going to close stdin (which stays open here): the
    renderer is stopped, then the signal ends the process as it would have anyway."""
    session.send_signal(signum)
    assert session.wait(60) == -signum
    _quiet(session.stderr.read())
