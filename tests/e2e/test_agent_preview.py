"""The agent's view of its own pending suggestions, in a real browser.

``view_pages`` in approval mode renders a preview through the same print route as export,
fed the document with the pending suggestions applied; the editor shows a batch of pending
inserts in the order it was written. Found by the 2026-11 real-model evaluation.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from conftest import api  # noqa: E402

from langstage_codraft.render import Renderer
from langstage_codraft.store import get_store
from langstage_codraft.tools import insert_blocks, page_shots, propose_delete, propose_edit

BATCH = [{"type": "heading", "content": "## Batch heading"}, {"type": "text", "content": "Batch paragraph one."},
         {"type": "text", "content": "Batch paragraph two."}]


def _doc(server, title):
    c = api(server)
    did = c.post("/api/docs", json={"title": title}).json()["id"]
    blocks = [c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": t}}).json()
              for t in ("Accepted first.", "Accepted second.", "Accepted third.")]
    return c, did, blocks


def _page_text(browser, url):
    ctx = browser.new_context()
    pg = ctx.new_page()
    try:
        pg.goto(url)
        pg.wait_for_function("window.__codraftReady === true", timeout=30000)
        return [el.inner_text().strip() for el in pg.query_selector_all(".cd-item")]
    finally:
        ctx.close()


def test_the_preview_renders_pending_suggestions_through_the_print_route(server, browser):
    c, did, (a, b, third) = _doc(server, "Preview")
    cfg = {"configurable": {"thread_id": did}}
    insert_blocks.invoke({"blocks": BATCH, "after_block_id": a["id"]}, config=cfg)
    propose_edit.invoke({"block_id": b["id"], "content": "Second, as proposed."}, config=cfg)
    propose_delete.invoke({"block_id": third["id"]}, config=cfg)

    plain = _page_text(browser, f"{server}/print/{did}")
    assert plain == ["Accepted first.", "Accepted second.", "Accepted third."]  # export is untouched
    preview = _page_text(browser, f"{server}/print/{did}?preview=pending")
    assert preview == ["Accepted first.", "Batch heading", "Batch paragraph one.", "Batch paragraph two.",
                       "Second, as proposed."]

    sugs = [s["id"] for s in get_store().list_suggestions(did) if s["kind"] == "insert"]
    with tempfile.TemporaryDirectory() as td:
        r = Renderer(server, Path(td))
        try:
            text, shots = page_shots(r, did)
            assert "PREVIEW" in text and "5 pending suggestion(s)" in text
            assert shots[0]["blocks"] == [a["id"], *sugs, b["id"]]
            for sid in sugs:
                assert f"{sid} (insert)" in text
            assert f"{b['id']} (edit)" in text and f"pending delete: {third['id']}" in text
            assert Path(shots[0]["path"]).stat().st_size > 2000
            text, shots = page_shots(r, did, pending=False)
            assert "PREVIEW" not in text and shots[0]["blocks"] == [a["id"], b["id"], third["id"]]
        finally:
            r.close()
    # looking changed nothing: the suggestions are all still pending, the document as it was
    assert len(get_store().list_suggestions(did)) == 5
    assert [x["content"]["markdown"] for x in c.get(f"/api/docs/{did}").json()["blocks"]] == [
        "Accepted first.", "Accepted second.", "Accepted third."]


def test_the_editor_shows_a_pending_batch_in_order_and_accept_all_keeps_it(page, server):
    c, did, (a, b, third) = _doc(server, "Batch order")
    insert_blocks.invoke({"blocks": BATCH, "after_block_id": a["id"]}, config={"configurable": {"thread_id": did}})
    page.goto(f"{server}/d/{did}")
    page.wait_for_selector(".ed-item.virtual")
    shown = [el.inner_text().strip().splitlines()[0] for el in page.query_selector_all(".ed-item")]
    shown = [t for t in shown if t.startswith(("Accepted", "Batch"))]
    assert shown == ["Accepted first.", "Batch heading", "Batch paragraph one.", "Batch paragraph two.",
                     "Accepted second.", "Accepted third."]
    page.click("[data-testid=tab-review]")
    page.get_by_text("Accept all").click()
    page.wait_for_selector(".ed-item.virtual", state="detached")
    got = [x["content"].get("markdown") or x["content"].get("text") for x in c.get(f"/api/docs/{did}").json()["blocks"]]
    assert got == ["Accepted first.", "Batch heading", "Batch paragraph one.", "Batch paragraph two.",
                   "Accepted second.", "Accepted third."]
