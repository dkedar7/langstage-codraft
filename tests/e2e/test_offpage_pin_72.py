"""A block pinned off the page, measured in a real browser (gh #72).

A pin past the page's left, right or top edge dropped its block out of every export while
``layout-check`` said ok and ``export --strict`` passed: the print view only flagged a pin past
the bottom. Now such a block counts as cut off on any side, an image may still bleed while at
least half of it is on the page, and the agent's tool refuses both an off-page pin and a
``pin_page`` past the last page.
"""
from __future__ import annotations

import pytest
from conftest import api  # noqa: E402

from langstage_codraft.tools import set_block_layout


def _cfg(doc_id: str) -> dict:
    return {"configurable": {"thread_id": doc_id}}


def _pin(x, y, w=300, h=200):
    return {"pin": {"anchor": "page", "page": 1, "x": x, "y": y, "w": w, "h": h}}


def _doc(c, title, layout=None, kind="text", content=None):
    did = c.post("/api/docs", json={"title": title}).json()["id"]
    c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Visible paragraph."}})
    b = c.post(f"/api/docs/{did}/blocks", json={"type": kind, "content": content or {"markdown": "Author paragraph."},
                                                "layout": layout}).json()
    return did, b["id"]


@pytest.mark.parametrize("xy", [(-2000, 100), (100, -2000), (5000, 100), (100, 5000), (700, 100)])
def test_a_block_already_pinned_off_the_page_is_reported_cut_off(server, xy):
    c = api(server)
    did, bid = _doc(c, f"off {xy}", _pin(*xy))   # as an older version (or a hand edit) left it
    check = c.get(f"/api/docs/{did}/layout-check").json()
    assert check["ok"] is False and [x["block_id"] for x in check["cut_off"]] == [bid]
    r = c.get(f"/api/docs/{did}/export", params={"format": "pdf"})
    assert r.status_code == 200 and r.headers.get("x-codraft-cut-off") == f"{bid}@1"
    assert c.get(f"/api/docs/{did}/export", params={"format": "pdf", "strict": "true"}).status_code == 409


def test_a_pin_on_the_page_and_an_image_bleed_are_not_cut_off(server):
    c = api(server)
    did, _ = _doc(c, "inside", _pin(100, 100))
    assert c.get(f"/api/docs/{did}/layout-check").json() == {"ok": True, "cut_off": []}
    svg = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'/>"
    did, _ = _doc(c, "bleed", _pin(-20, -20, 856, 300), kind="image", content={"src": svg, "alt": "hero"})
    assert c.get(f"/api/docs/{did}/layout-check").json() == {"ok": True, "cut_off": []}
    did, bid = _doc(c, "mostly off", _pin(-250, 100, 300, 200), kind="image", content={"src": svg, "alt": "x"})
    assert [x["block_id"] for x in c.get(f"/api/docs/{did}/layout-check").json()["cut_off"]] == [bid]


def test_the_agent_cannot_pin_off_the_page_or_past_the_last_page(server):
    c = api(server)
    did, bid = _doc(c, "agent")
    out = set_block_layout.func(block_id=bid, pin_page=1, page_x_px=-2000, page_y_px=100, page_w_px=300,
                                page_h_px=200, config=_cfg(did))
    assert out.startswith("ERROR: REFUSED") and "The page is 816 x 1056 px" in out, out
    out = set_block_layout.func(block_id=bid, pin_page=40, page_x_px=100, page_y_px=100, page_w_px=300,
                                page_h_px=200, config=_cfg(did))
    assert out.startswith("ERROR: REFUSED") and "has 1 page," in out, out
    assert c.get(f"/api/docs/{did}").json()["blocks"][1]["layout"]["pin"] is None
    out = set_block_layout.func(block_id=bid, pin_page=1, page_x_px=100, page_y_px=100, page_w_px=300,
                                page_h_px=200, config=_cfg(did))
    assert out.startswith("Layout of"), out
    assert c.get(f"/api/docs/{did}/layout-check").json()["ok"] is True
