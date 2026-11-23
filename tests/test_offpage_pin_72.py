"""A pin can't take a block off the page (gh #72).

``set_block_layout`` checked a pin's size (gh #58) but not where it goes, so an agent could pin
the author's paragraph at ``page_x_px=-2000`` and it dropped out of every export with no
warning. The tool now refuses a page-anchored pin that puts a text-bearing block (partly) off
the page, lets an image or shape bleed only while at least half of it is on the page, and
refuses a ``pin_page`` past the document's last page.

The print view's side (an off-page pin counts as cut off) needs Chromium:
tests/e2e/test_offpage_pin_72.py.
"""
from __future__ import annotations

import re

import pytest

from langstage_codraft import render
from langstage_codraft.tools import propose_insert, set_block_layout

# letter at 96 dpi
W, H = 816, 1056


class FakeRenderer:
    """Stands in for Chromium: nothing is cut off, and the document has ``pages`` body pages."""

    def __init__(self, pages=1):
        self.pages, self.counted = pages, 0

    def call(self, value):
        return value

    def cut_off(self, doc_id, preview=False):
        return []

    def body_page_count(self, doc_id):
        self.counted += 1
        return self.pages


@pytest.fixture
def renderer(monkeypatch):
    def use(pages=1):
        r = FakeRenderer(pages)
        monkeypatch.setattr(render, "get_renderer", lambda: r)
        return r

    return use


def _doc(store, kind="text", content=None):
    d = store.create_document("Pins")
    store.insert_block(d["id"], "text", {"markdown": "Visible paragraph."}, author="author")
    b = store.insert_block(d["id"], kind, content or {"markdown": "Author paragraph must print."}, author="author")
    return d, b


def _pin(cfg, bid, **kw):
    args = {"pin_page": 1, "page_w_px": 300, "page_h_px": 200, **kw}
    return set_block_layout.func(block_id=bid, config=cfg, **args)


@pytest.mark.parametrize("xy", [
    {"page_x_px": -2000, "page_y_px": 100},
    {"page_x_px": 100, "page_y_px": -2000},
    {"page_x_px": 5000, "page_y_px": 100},
    {"page_x_px": 100, "page_y_px": 5000},
    {"page_x_px": 600, "page_y_px": 100},    # partly off the right edge (600 + 300 > 816)
    {"page_x_px": -10, "page_y_px": 100},    # just past the left edge
])
def test_a_pin_that_puts_text_off_the_page_is_refused(store, cfg_for, renderer, xy):
    renderer()
    d, b = _doc(store)
    out = _pin(cfg_for(d["id"]), b["id"], **xy)
    assert out.startswith("ERROR: REFUSED, nothing changed") and b["id"] in out, out
    assert f"The page is {W} x {H} px" in out and "does not print" in out
    assert store.get_block(b["id"])["layout"]["pin"] is None


def test_a_pin_wholly_on_the_page_is_fine_edges_included(store, cfg_for, renderer):
    renderer()
    d, b = _doc(store)
    out = _pin(cfg_for(d["id"]), b["id"], page_x_px=0, page_y_px=H - 200, page_w_px=W)
    assert out.startswith("Layout of"), out
    pin = store.get_block(b["id"])["layout"]["pin"]
    assert pin == {"anchor": "page", "page": 1, "x": 0, "y": H - 200, "w": W, "h": 200}


def test_an_image_may_bleed_but_not_mostly_off_the_page(store, cfg_for, renderer):
    renderer()
    d, b = _doc(store, "image", {"src": "", "alt": "hero"})
    cfg = cfg_for(d["id"])
    # edge-to-edge hero art that bleeds 20 px past three edges: allowed
    out = _pin(cfg, b["id"], page_x_px=-20, page_y_px=-20, page_w_px=W + 40, page_h_px=400)
    assert out.startswith("Layout of"), out
    # two thirds of it off the left edge: refused, and the bleed pin stays
    out = _pin(cfg, b["id"], page_x_px=-200, page_y_px=100, page_w_px=300, page_h_px=200)
    assert out.startswith("ERROR: REFUSED") and "at least half of it must be on the page" in out, out
    assert store.get_block(b["id"])["layout"]["pin"]["x"] == -20


def test_a_pin_page_past_the_last_page_is_refused(store, cfg_for, renderer):
    r = renderer(pages=2)
    d, b = _doc(store)
    cfg = cfg_for(d["id"])
    out = _pin(cfg, b["id"], pin_page=40, page_x_px=100, page_y_px=100)
    assert out.startswith("ERROR: REFUSED") and "has 2 pages" in out and "from 1 to 2" in out, out
    assert store.get_block(b["id"])["layout"]["pin"] is None
    assert _pin(cfg, b["id"], pin_page=2, page_x_px=100, page_y_px=100).startswith("Layout of")
    # a column pin is held to the same page range
    out = set_block_layout.func(block_id=b["id"], pin_page=3, pin_top_px=0, config=cfg)
    assert out.startswith("ERROR: REFUSED") and "pin_page 3" in out, out
    assert r.counted >= 2


def test_without_a_renderer_the_page_range_is_not_asserted(store, cfg_for, monkeypatch):
    monkeypatch.setattr(render, "get_renderer", lambda: None)
    monkeypatch.setattr(render, "_FACTORY", None)
    d, b = _doc(store)
    out = _pin(cfg_for(d["id"]), b["id"], pin_page=3, page_x_px=100, page_y_px=100)
    assert out.startswith("Layout of"), out
    # the position check needs no renderer
    out = _pin(cfg_for(d["id"]), b["id"], page_x_px=-2000, page_y_px=100)
    assert out.startswith("ERROR: REFUSED"), out


def test_an_unchanged_pin_does_not_block_an_unrelated_restyle(store, cfg_for, renderer):
    renderer()
    d = store.create_document("Old")
    # pinned off the page by hand, or by an older version
    b = store.insert_block(d["id"], "text", {"markdown": "Old pin."}, author="author",
                           layout={"pin": {"anchor": "page", "page": 1, "x": -2000, "y": 100, "w": 300, "h": 200}})
    out = set_block_layout.func(block_id=b["id"], variant="small", config=cfg_for(d["id"]))
    assert out.startswith("Layout of"), out


def test_the_agent_cannot_pin_its_own_pending_insert_off_the_page(store, cfg_for, renderer):
    renderer()
    d, _ = _doc(store)
    cfg = cfg_for(d["id"])
    out = propose_insert.func(block_type="text", content="A new paragraph.", config=cfg)
    sid = re.search(r"sug_[0-9a-z]+", out).group(0)
    out = _pin(cfg, sid, page_x_px=-2000, page_y_px=100)
    assert out.startswith("ERROR: REFUSED"), out
