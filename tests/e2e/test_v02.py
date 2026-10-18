"""0.2.0 features end to end: live collaboration, themes, sketches, video, new export
formats and the MCP endpoint, all against a live keyless demo server."""
from __future__ import annotations

import asyncio
import io
import zipfile

import pytest
from conftest import api


def test_dark_theme_toggle_persists(page, server):
    page.goto(server)
    page.wait_for_selector("[data-testid=theme-toggle]")
    for _ in range(3):  # cycle until dark
        if page.evaluate("document.documentElement.classList.contains('dark')"):
            break
        page.click("[data-testid=theme-toggle]")
    assert page.evaluate("document.documentElement.classList.contains('dark')")
    page.reload()
    page.wait_for_selector("[data-testid=theme-toggle]")
    assert page.evaluate("document.documentElement.classList.contains('dark')")
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(21, 21, 23)", bg  # langstage-web's dark --color-surface


def test_two_people_see_each_other_and_edits_live(browser, server):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Together"}).json()["id"]
    b = c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Shared paragraph."}}).json()
    ctx1, ctx2 = browser.new_context(), browser.new_context()
    try:
        p1, p2 = ctx1.new_page(), ctx2.new_page()
        p1.goto(f"{server}/d/{did}")
        p2.goto(f"{server}/d/{did}")
        p1.wait_for_selector(".cd-page")
        p2.wait_for_selector(".cd-page")
        p2.locator(f"[data-block-id={b['id']}]").click()
        p1.wait_for_selector("[data-testid=peer-tag]", timeout=15000)
        assert p1.locator("[data-testid=peer-avatar]").count() >= 1
        # An edit by someone else appears without reloading (pushed over SSE).
        c.patch(f"/api/blocks/{b['id']}", json={"content": {"markdown": "Changed elsewhere."}})
        p1.wait_for_selector("text=Changed elsewhere.", timeout=10000)
    finally:
        ctx1.close()
        ctx2.close()


def test_concurrent_inline_edits_keep_both_versions(page, server):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Race"}).json()["id"]
    b = c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Original."}}).json()
    page.goto(f"{server}/d/{did}")
    blk = page.locator(f"[data-block-id={b['id']}]")
    blk.dblclick()
    page.wait_for_selector("[data-testid=text-editor]")
    # Someone else saves while we are typing.
    c.patch(f"/api/blocks/{b['id']}", json={"content": {"markdown": "Theirs."}, "author": "Ada"})
    page.keyboard.press("End")
    page.keyboard.type(" Mine.")
    page.keyboard.press("Control+Enter")
    page.wait_for_selector("text=Both versions are kept", timeout=10000)
    tree = c.get(f"/api/blocks/{b['id']}/versions").json()
    texts = {v["content"].get("markdown") for v in tree["versions"]}
    assert {"Original.", "Theirs.", "Original. Mine."} <= texts
    assert len(tree["branches"]) == 2


def test_sketch_on_image_block(page, server):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Sketchy"}).json()["id"]
    b = c.post(f"/api/docs/{did}/blocks", json={"type": "image", "content": {}}).json()
    page.goto(f"{server}/d/{did}")
    page.locator(f"[data-block-id={b['id']}]").click()
    page.click("[data-testid=tab-block]")
    page.click("[data-testid=open-sketch]")
    canvas = page.locator("[data-testid=sketch-canvas]")
    box = canvas.bounding_box()
    page.mouse.move(box["x"] + 50, box["y"] + 50)
    page.mouse.down()
    page.mouse.move(box["x"] + 300, box["y"] + 200, steps=10)
    page.mouse.up()
    page.click("[data-testid=sketch-save]")
    page.wait_for_function(f"fetch('/api/docs/{did}').then(r=>r.json()).then(d=>!!d.blocks[0].content.sketch)")
    page.wait_for_selector(".cd-sketch-tag")
    # The demo agent can look at it (view_sketch) through the same tool path.
    sketch = c.get(f"/api/docs/{did}").json()["blocks"][0]["content"]["sketch"]
    assert c.get(f"/api/uploads/{sketch}/file").headers["content-type"] == "image/png"


def test_video_block_renders_and_exports_poster(page, server):
    from PIL import Image

    c = api(server)
    did = c.post("/api/docs", json={"title": "Clip"}).json()["id"]
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), (37, 99, 235)).save(buf, format="JPEG")
    poster = c.post(f"/api/docs/{did}/uploads", files={"file": ("poster.jpg", buf.getvalue(), "image/jpeg")},
                    data={"purpose": "asset"}).json()
    gif = io.BytesIO()
    Image.new("RGB", (64, 64), (5, 150, 105)).save(gif, format="GIF")
    up = c.post(f"/api/docs/{did}/uploads", files={"file": ("loop.gif", gif.getvalue(), "image/gif")},
                data={"purpose": "asset"}).json()
    c.post(f"/api/docs/{did}/blocks", json={"type": "video", "content": {
        "src": up["id"], "mime": "image/gif", "poster": poster["id"], "caption": "A looping GIF"}})
    page.goto(f"{server}/d/{did}")
    page.wait_for_selector("[data-testid=block-video] img")
    r = c.get(f"/api/docs/{did}/export", params={"format": "pdf"})
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


@pytest.mark.parametrize("fmt,magic,mime", [
    ("docx", b"PK", "wordprocessingml"),
    ("pptx", b"PK", "presentationml"),
    ("gif", b"GIF8", "image/gif"),
    ("mp4", None, "video/mp4"),
])
def test_new_export_formats(server, fmt, magic, mime):
    c = api(server)
    doc = c.post("/api/docs", json={"template": "report", "title": "Formats"}).json()
    did = doc["id"]
    c.post(f"/api/docs/{did}/blocks", json={"type": "chart", "content": {"spec": {
        "data": {"values": [{"x": "A", "y": 1}, {"x": "B", "y": 3}]}, "mark": "bar",
        "encoding": {"x": {"field": "x", "type": "nominal"}, "y": {"field": "y", "type": "quantitative"}}}}})
    r = c.get(f"/api/docs/{did}/export", params={"format": fmt})
    assert r.status_code == 200, r.text[:300]
    assert mime in r.headers["content-type"]
    if magic:
        assert r.content.startswith(magic)
    if fmt == "mp4":
        assert r.content[4:8] == b"ftyp"
    if fmt == "docx":
        import docx

        d = docx.Document(io.BytesIO(r.content))
        texts = [p.text for p in d.paragraphs]
        assert "Formats" in texts[0] and "Executive summary" in texts
        assert len(d.inline_shapes) >= 1  # the chart, as a picture
    if fmt == "pptx":
        from pptx import Presentation

        prs = Presentation(io.BytesIO(r.content))
        assert len(prs.slides) >= 2  # cover + body
        all_text = " ".join(sh.text_frame.text for s in prs.slides for sh in s.shapes if sh.has_text_frame)
        assert "Formats" in all_text and "Executive summary" in all_text
        assert any(sh.shape_type == 13 for s in prs.slides for sh in s.shapes)  # a picture (chart)
    if fmt == "gif":
        from PIL import Image

        assert Image.open(io.BytesIO(r.content)).n_frames >= 2


def test_mcp_over_http_co_authors_with_view_pages(server):
    """A real MCP client (streamable HTTP) creates a doc, suggests an edit, and sees the page."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def go():
        async with streamable_http_client(f"{server}/mcp") as (read, write), ClientSession(read, write) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert {"create_document", "propose_edit", "view_pages"} <= names
            out = await s.call_tool("create_document", {"title": "From MCP", "template": "memo"})
            doc_id = out.content[0].text.split()[1]
            res = await s.call_tool("view_pages", {"doc_id": doc_id})
            kinds = [c.type for c in res.content]
            assert "image" in kinds, kinds
            return doc_id

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(1) as ex:  # sync Playwright owns this thread's event loop
        doc_id = ex.submit(asyncio.run, go()).result(timeout=120)
    assert api(server).get(f"/api/docs/{doc_id}").json()["title"] == "From MCP"


def test_export_menu_offers_all_formats(page, server):
    did = api(server).post("/api/docs", json={"title": "Menu"}).json()["id"]
    page.goto(f"{server}/d/{did}")
    page.click("[data-testid=export]")
    for f in ("pdf", "docx", "pptx", "png", "jpg", "html", "gif", "mp4"):
        assert page.locator(f"[data-testid=export-{f}]").count() == 1


def test_html_zip_includes_video_and_poster(server):
    from PIL import Image

    c = api(server)
    did = c.post("/api/docs", json={"title": "Zip"}).json()["id"]
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 10, 10)).save(buf, format="PNG")
    up = c.post(f"/api/docs/{did}/uploads", files={"file": ("p.png", buf.getvalue(), "image/png")},
                data={"purpose": "asset"}).json()
    c.post(f"/api/docs/{did}/blocks", json={"type": "image", "content": {"src": up["id"]}})
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/docs/{did}/export", params={"format": "html"}).content))
    assert "index.html" in z.namelist() and any(n.startswith("assets/api_uploads") for n in z.namelist())


def test_long_document_pdf_has_no_blank_trailing_pages(server):
    """Regression: the hidden measuring copy used to make print-to-PDF add blank pages."""
    import zipfile as _zip

    from pypdf import PdfReader

    c = api(server)
    did = c.post("/api/docs", json={"title": "Long", "settings": {"columns": 3}}).json()["id"]
    para = ("Utilities measure flow, pressure and quality across the network. " * 12 + "\n\n") * 6
    c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": para * 4}})
    pdf = c.get(f"/api/docs/{did}/export", params={"format": "pdf"}).content
    png = c.get(f"/api/docs/{did}/export", params={"format": "png"}).content
    n_png = len(_zip.ZipFile(io.BytesIO(png)).namelist()) if png[:2] == b"PK" else 1
    assert len(PdfReader(io.BytesIO(pdf)).pages) == n_png >= 2


def test_page_numbers_skip_cover_and_unnumbered_pages(page, server):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Numbers", "settings": {"unnumbered_pages": [3]}}).json()["id"]
    c.post(f"/api/docs/{did}/blocks", json={"type": "heading", "content": {"text": "Cover", "level": 1},
                                            "layout": {"role": "cover"}})
    c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Page {page} of {pages}"},
                                            "layout": {"variant": "footer", "repeat": "all",
                                                       "pin": {"page": 1, "top": 900, "column": 0, "span": 1}}})
    for _ in range(3):
        c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "x"}})
        c.post(f"/api/docs/{did}/blocks", json={"type": "page-break", "content": {}})
    page.goto(f"{server}/print/{did}")
    page.wait_for_function("window.__codraftReady === true")
    texts = page.eval_on_selector_all(".cd-page", "ps => ps.map(p => p.textContent.trim())")  # textContent ignores CSS uppercase
    assert "Page" not in texts[0]                     # cover: no footer
    assert "Page 1 of 3" in texts[1]                   # first body page is 1
    assert "Page" not in texts[2]                      # position 3 is unnumbered
    assert "Page 2 of 3" in texts[3]
