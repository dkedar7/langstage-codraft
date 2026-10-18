"""Browser end-to-end tests against a live demo server (keyless).

Skipped automatically when the built UI or Chromium is missing. Run:
    cd frontend && npm run build && cd .. && pytest tests/e2e
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "langstage_codraft" / "static" / "index.html"
pytestmark = pytest.mark.skipif(not STATIC.is_file(), reason="frontend not built")

LOREM = ("Agents increasingly produce documents rather than chat replies, yet most hosts still treat "
         "their output as a transcript. A document should be co-authored: the person owns the argument "
         "and the agent owns the presentation. ")


from conftest import api  # noqa: E402


def test_create_from_dialog_with_cover_page(page, server):
    page.goto(server)
    page.click("[data-testid=new-doc]")
    page.click("[data-testid=template-report]")
    page.fill("[data-testid=new-title]", "Quarterly review")
    page.click("[data-testid=create-doc]")
    page.wait_for_selector(".cd-page-cover")
    assert page.locator(".ed-page-label", has_text="Cover").count() == 1
    assert page.locator("[data-testid=doc-tab]", has_text="Quarterly review").count() == 1
    assert "/d/doc_" in page.url


def test_inline_edit_creates_version_and_step_back(page, server):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Edit me"}).json()["id"]
    page.goto(f"{server}/d/{did}")
    page.click("[data-testid=add-block]")
    page.click("[data-testid=add-text]")
    page.wait_for_selector("[data-testid=text-editor]")
    page.keyboard.type("Hello from the author.")
    page.keyboard.press("Control+Enter")
    page.wait_for_function(f"""fetch('/api/docs/{did}').then(r=>r.json()).then(d=>d.blocks[0]?.content.markdown==='Hello from the author.')""")
    blk = c.get(f"/api/docs/{did}").json()["blocks"][0]
    assert blk["n_versions"] == 2
    page.locator("[data-block-id]").first.click()
    page.click("[data-testid=tab-block]")
    page.click("[data-testid=version-back]")
    page.wait_for_function(f"""fetch('/api/docs/{did}').then(r=>r.json()).then(d=>d.blocks[0].content.markdown==='')""")


def test_comment_agent_suggestion_accept(page, server):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Comments"}).json()["id"]
    b = c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "First. Second. Third. Fourth."}}).json()
    page.goto(f"{server}/d/{did}")
    page.locator(f"[data-block-id={b['id']}]").click()
    page.click("[data-testid=tab-comments]")
    page.fill("[data-testid=comment-input]", "make it shorter")
    page.click("[data-testid=comment-add]")
    page.click("[data-testid=address-all]")
    page.wait_for_selector("[data-testid=suggestion-badge]", timeout=20000)
    # The text is unchanged until the author approves.
    assert c.get(f"/api/docs/{did}").json()["blocks"][0]["content"]["markdown"] == "First. Second. Third. Fourth."
    page.click("[data-testid=suggestion-badge]")
    page.click("[data-testid=accept-suggestion]")
    page.wait_for_function(f"""fetch('/api/docs/{did}').then(r=>r.json()).then(d=>d.blocks[0].content.markdown==='First. Second.')""")
    doc = c.get(f"/api/docs/{did}").json()
    assert doc["blocks"][0]["author"] == "agent"
    assert doc["comments"][0]["replies"], "agent replied to the comment"


def test_agent_insert_shows_ghost_block_then_accept(page, server):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Draft"}).json()["id"]
    page.goto(f"{server}/d/{did}")
    page.fill("[data-testid=chat-input]", "write a paragraph about tidal energy")
    page.click("[data-testid=chat-send]")
    page.wait_for_selector(".ed-item.virtual", timeout=20000)
    page.click("[data-testid=accept-insert]")
    page.wait_for_function(f"""fetch('/api/docs/{did}').then(r=>r.json()).then(d=>d.blocks.length===1)""")
    assert "tidal energy" in c.get(f"/api/docs/{did}").json()["blocks"][0]["content"]["markdown"]


def test_repeating_masthead_and_columns(page, server):
    c = api(server)
    doc = c.post("/api/docs", json={"template": "newsletter"}).json()
    did = doc["id"]
    mast = next(b for b in doc["blocks"] if b["layout"].get("repeat") == "all")
    for _ in range(6):
        c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": (LOREM * 4 + "\n\n") * 3}})
    page.goto(f"{server}/d/{did}")
    page.wait_for_selector(".cd-page")
    page.wait_for_timeout(800)
    n_pages = page.locator(".cd-page").count()
    assert n_pages >= 2
    assert page.locator(f".cd-page [data-block-id={mast['id']}]").count() == n_pages
    # three distinct column x positions on page 1
    xs = page.eval_on_selector_all(".cd-page[data-page='1'] .ed-item:not(.pinned)", "els => [...new Set(els.map(e => e.style.left))]")
    assert len(xs) == 3


def test_drag_to_reorder(page, server):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Order"}).json()["id"]
    a = c.post(f"/api/docs/{did}/blocks", json={"type": "heading", "content": {"text": "Alpha", "level": 2}}).json()
    c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": LOREM}})
    z = c.post(f"/api/docs/{did}/blocks", json={"type": "heading", "content": {"text": "Zulu", "level": 2}}).json()
    page.goto(f"{server}/d/{did}")
    target = page.locator(f"[data-block-id={a['id']}]")
    src = page.locator(f"[data-block-id={z['id']}]")
    src.hover()
    handle = src.locator("[data-testid=drag-handle]")
    hb = handle.bounding_box()
    tb = target.bounding_box()
    page.mouse.move(hb["x"] + 5, hb["y"] + 5)
    page.mouse.down()
    page.mouse.move(tb["x"] + 40, tb["y"] + 4, steps=8)
    page.mouse.up()
    page.wait_for_function(f"""fetch('/api/docs/{did}').then(r=>r.json()).then(d=>d.blocks[0].id==='{z['id']}')""")


def test_editor_matches_export_pixels(page, server):
    """Parity gate: the page in the editor and the exported PNG are the same render."""
    from PIL import Image, ImageChops

    c = api(server)
    doc = c.post("/api/docs", json={"template": "paper", "title": "Parity"}).json()
    did = doc["id"]
    t = next(b for b in doc["blocks"] if b["type"] == "text" and "Motivation" in b["content"]["markdown"])
    c.patch(f"/api/blocks/{t['id']}", json={"content": {"markdown": (LOREM * 3 + "\n\n") * 3}})
    exp = c.get(f"/api/docs/{did}/export", params={"format": "png"})
    assert exp.status_code == 200, exp.text
    import zipfile

    if exp.headers["content-type"] == "application/zip":
        z = zipfile.ZipFile(io.BytesIO(exp.content))
        first = sorted(z.namelist())[0]
        exported = Image.open(io.BytesIO(z.read(first))).convert("RGB")
    else:
        exported = Image.open(io.BytesIO(exp.content)).convert("RGB")
    ctx = page.context.browser.new_context(viewport={"width": 1800, "height": 1400}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(f"{server}/d/{did}")
    pg.wait_for_selector(".cd-page[data-page='1']")
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(1500)
    # zoom 100% so editor pixels are comparable
    while pg.locator("text=100%").count() == 0:
        label = pg.locator(".w-10.text-center").inner_text()
        pg.click("[title='Zoom in']" if int(label.rstrip('%')) < 100 else "[title='Zoom out']")
    pg.wait_for_timeout(500)
    pg.mouse.move(0, 0)
    editor = Image.open(io.BytesIO(pg.locator(".cd-page[data-page='1']").screenshot())).convert("RGB")
    ctx.close()
    assert editor.size == exported.size, (editor.size, exported.size)
    diff = ImageChops.difference(editor, exported).convert("L")
    changed = sum(1 for v in diff.getdata() if v > 48)
    ratio = changed / (diff.size[0] * diff.size[1])
    assert ratio < 0.01, f"{ratio:.4%} of pixels differ between editor and export"


def test_kickoff_stays_visible_when_history_is_slow_and_mid_turn_messages_queue(page, server):
    """Dogfood 0.4.2: behind a slower proxy, the chat history landed on top of a new
    document's kickoff and hid it; and Enter during a turn silently did nothing."""
    import time

    def slow_history(route):
        time.sleep(1.5)
        route.continue_()

    page.route("**/api/docs/*/messages", slow_history)
    page.goto(server)
    page.click("[data-testid=new-doc]")
    page.fill("textarea", "A one-page note on library opening hours")
    page.click("[data-testid=create-doc]")
    log = page.locator("[data-testid=chat-log]")
    log.get_by_text("Here is what I want to write").wait_for(timeout=15000)
    # Queue a second message while the kickoff turn is running.
    page.fill("[data-testid=chat-input]", "List the references")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert page.locator("[data-testid=chat-queued]").count() == 1 or log.get_by_text("List the references", exact=True).count() == 1
    log.get_by_text("List the references", exact=True).wait_for(timeout=30000)
    assert log.get_by_text("Here is what I want to write").count() == 1
