"""Rendering without a running server, for real (gh #44): the in-process renderer gives
the same pages as a live server, from a second process on the same data directory, over
`export --data-dir`, `export_document` and the stdio MCP server's `view_pages`."""
from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageChops

from langstage_codraft.store import Store

PNG_1PX = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
           b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND"
           b"\xaeB`\x82")


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


def _cli(*args: str, cwd: Path, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "langstage_codraft.cli", *args], cwd=cwd, env=_env(),
                          capture_output=True, text=True, timeout=timeout)


@pytest.fixture
def data(tmp_path):
    """A data directory with a two-page-ish report and a one-page memo, made offline."""
    d = tmp_path / ".codraft"
    s = Store(d / "codraft.db")
    report = s.create_document("Field report")
    s.insert_block(report["id"], "heading", {"text": "Findings", "level": 1})
    s.insert_block(report["id"], "text", {"markdown": "The **quick** brown fox. " * 40})
    memo = s.create_document("Memo")
    s.insert_block(memo["id"], "text", {"markdown": "One line, one page."})
    up = s.add_upload(memo["id"], "dot.png", PNG_1PX, purpose="asset", mime="image/png")
    s.insert_block(memo["id"], "image", {"src": up["url"], "alt": "a dot"})
    s.close()
    return tmp_path, report["id"], memo["id"]


def _same_pixels(a: bytes, b: bytes) -> bool:
    ia, ib = Image.open(io.BytesIO(a)).convert("RGB"), Image.open(io.BytesIO(b)).convert("RGB")
    return ia.size == ib.size and ImageChops.difference(ia, ib).getbbox() is None


@pytest.fixture
def live(data):
    """`langstage-codraft run` on the same data directory, in its own process."""
    cwd, *_ = data
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen([sys.executable, "-m", "langstage_codraft.cli", "run", "--no-browser", "--host",
                             "127.0.0.1", "--port", str(port), "--data-dir", str(cwd / ".codraft")],
                            cwd=cwd, env=_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(150):
            try:
                httpx.get(base + "/api/health", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            pytest.fail("the live server did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=20)


def test_cli_exports_from_the_data_dir_with_no_server(data):
    cwd, report, memo = data
    res = _cli("export", report, memo, "-f", "pdf", "-f", "png", "-f", "docx", "-o", "out",
               "--data-dir", ".codraft", cwd=cwd)
    assert res.returncode == 0, res.stdout + res.stderr
    out = cwd / "out"
    import pypdf

    pdf = pypdf.PdfReader(str(out / "field-report.pdf"))
    assert "Findings" in pdf.pages[0].extract_text()
    assert len(pypdf.PdfReader(str(out / "memo.pdf")).pages) == 1
    assert Image.open(out / "memo.png").size == (1632, 2112)  # Letter at 2x, as a live server gives
    import docx

    assert any("quick" in p.text for p in docx.Document(str(out / "field-report.docx")).paragraphs)
    assert res.stdout.count("[ ok ] wrote") == 6


def test_second_process_renders_the_same_pages_while_a_server_is_live(data, live):
    """The live server and an in-process render in another process share the data
    directory; both go through the same print route, so the pixels match."""
    cwd, report, memo = data
    through_server = httpx.get(f"{live}/api/docs/{memo}/export", params={"format": "png"}, timeout=120)
    assert through_server.status_code == 200
    res = _cli("export", memo, "-f", "png", "-o", "memo.png", "--data-dir", ".codraft", cwd=cwd)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _same_pixels(through_server.content, (cwd / "memo.png").read_bytes())

    # An edit made through the live server is what the other process exports next.
    r = httpx.post(f"{live}/api/docs/{memo}/blocks", json={"type": "text", "content": {"markdown": "Added live."}})
    assert r.status_code == 200, r.text
    res = _cli("export", memo, "-f", "docx", "-o", "memo.docx", "--standalone", "--data-dir", ".codraft", cwd=cwd)
    assert res.returncode == 0, res.stdout + res.stderr
    import docx

    assert any("Added live." in p.text for p in docx.Document(str(cwd / "memo.docx")).paragraphs)
    # ...and the server is still serving and still has both documents.
    assert {d["id"] for d in httpx.get(f"{live}/api/docs").json()} >= {report, memo}


def test_export_document_and_standalone_from_python(data):
    from langstage_codraft import ExportError, Standalone, export_document

    cwd, report, memo = data
    d = cwd / ".codraft"
    pdf = export_document(d, memo)
    assert isinstance(pdf, bytes) and pdf.startswith(b"%PDF")
    path = export_document(d, report, "html", cwd / "site" / "report")
    assert path == cwd / "site" / "report.zip"  # HTML comes zipped, and says so
    with Standalone(d) as codraft:  # one browser for all of these
        png = codraft.export(memo, "png")
        assert Image.open(io.BytesIO(png)).size == (1632, 2112)
        z = zipfile.ZipFile(io.BytesIO(codraft.export(memo, "html")))
        # The snapshot's assets are fetched with the renderer's credentials: the image is in.
        assert any(n.startswith("assets/api_uploads_") for n in z.namelist()), z.namelist()
        with pytest.raises(ExportError) as err:
            codraft.export("doc_nope")
        assert err.value.status == 404


def test_stdio_mcp_view_pages_with_no_server(data):
    """`langstage-codraft mcp --data-dir DIR` alone: view_pages returns the page."""
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    cwd, _, memo = data

    async def go():
        params = StdioServerParameters(
            command=sys.executable, env=_env(), cwd=str(cwd),
            args=["-m", "langstage_codraft.cli", "mcp", "--data-dir", str(cwd / ".codraft"), "--port", "1"])
        async with stdio_client(params) as streams, ClientSession(*streams[:2]) as s:
            await s.initialize()
            first = await s.call_tool("view_pages", {"doc_id": memo})
            again = await s.call_tool("view_pages", {"doc_id": memo})  # the renderer is kept
            out = await s.call_tool("list_documents", {})  # the protocol stream is intact
            return first, again, out

    first, again, out = asyncio.run(asyncio.wait_for(go(), 180))
    for res in (first, again):
        kinds = [c.type for c in res.content]
        assert kinds == ["text", "image"], [getattr(c, "text", c.type) for c in res.content]
        assert "Rendered page 1 of 1" in res.content[0].text
    assert "Memo" in out.content[0].text
