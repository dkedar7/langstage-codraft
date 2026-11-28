"""A suggested block's Accept / Reject bar never covers the block after it (gh #78).

The bar used to hang 30px below the suggested block, over whatever came next: in the usual
case, a paragraph drafted under one heading hid the start of the next heading. The editor now
keeps room for the bar below a pending insert; the exported pages never contain one.
"""
from __future__ import annotations

from conftest import api  # noqa: E402

from langstage_codraft.tools import insert_blocks


def _box(page, selector):
    el = page.locator(selector).first
    el.wait_for()
    return el.bounding_box()


def _overlap(a, b) -> bool:
    return not (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"]
                or a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"])


def test_the_bar_does_not_cover_the_next_heading(page, server):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Bar room"}).json()["id"]
    results = c.post(f"/api/docs/{did}/blocks", json={"type": "heading", "content": {"text": "Results", "level": 2}}).json()
    returns = c.post(f"/api/docs/{did}/blocks", json={"type": "heading", "content": {"text": "Returns", "level": 2}}).json()
    c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "The authors' closing paragraph."}})
    insert_blocks.invoke({"blocks": [{"type": "text", "content": "Revenue reached $4.82 million in Q3, up 17.6% from Q2."}],
                          "after_block_id": results["id"]}, config={"configurable": {"thread_id": did}})

    page.goto(f"{server}/d/{did}")
    page.wait_for_selector(".ed-item.virtual")
    bar = _box(page, ".ed-item.virtual .ed-sugg-bar")
    nxt = _box(page, f"[data-block-id='{returns['id']}'] .cd-h")
    sugg = _box(page, ".ed-item.virtual")
    assert not _overlap(bar, nxt), (bar, nxt)
    assert bar["y"] + bar["height"] <= nxt["y"] + 0.5
    # the bar is the suggested block's own: inside its box, below its text
    assert sugg["y"] <= bar["y"] and bar["y"] + bar["height"] <= sugg["y"] + sugg["height"] + 0.5

    # and it still works
    page.click(".ed-item.virtual [data-testid=accept-insert]")
    page.wait_for_selector(".ed-item.virtual", state="detached")
    got = [b["content"].get("markdown") or b["content"].get("text") for b in c.get(f"/api/docs/{did}").json()["blocks"]]
    assert got[1].startswith("Revenue reached")


def test_the_print_route_keeps_no_room_for_a_pending_insert(page, server):
    """The room is editor chrome: the exported page lays the document out as if it weren't there."""
    c = api(server)
    did = c.post("/api/docs", json={"title": "No room in print"}).json()["id"]
    first = c.post(f"/api/docs/{did}/blocks", json={"type": "heading", "content": {"text": "One", "level": 2}}).json()
    c.post(f"/api/docs/{did}/blocks", json={"type": "heading", "content": {"text": "Two", "level": 2}})

    def tops():
        page.goto(f"{server}/print/{did}")
        page.wait_for_function("window.__codraftReady === true", timeout=30000)
        return [el.bounding_box()["y"] for el in page.query_selector_all(".cd-item")]

    before = tops()
    insert_blocks.invoke({"blocks": [{"type": "text", "content": "Pending paragraph."}],
                          "after_block_id": first["id"]}, config={"configurable": {"thread_id": did}})
    assert tops() == before
