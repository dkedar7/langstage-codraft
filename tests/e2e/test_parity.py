"""0.5.5: editor and export lay out the same pages (gh #13).

Empty blocks show a hint in the editor ("Heading", "Type something...", the empty
References line). The export must measure the same box, or every block below lands
one line higher in the PDF and, near a page boundary, the page count differs.
"""
from __future__ import annotations

import io

import pypdf
from conftest import api

LAYOUT_JS = """() => [...document.querySelectorAll('[data-block-id]')].map(el => ({
  id: el.dataset.blockId, page: Number(el.closest('.cd-page').dataset.page),
  top: parseFloat(el.style.top), h: parseFloat(el.style.height)}))"""


def _layout(pg, url: str, ready: str):
    pg.goto(url)
    pg.wait_for_selector(ready)
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(1200)
    return sorted(((b["id"], b["page"], round(b["top"]), round(b["h"])) for b in pg.evaluate(LAYOUT_JS)),
                  key=lambda b: (b[1], b[2], b[0]))


def test_empty_blocks_paginate_alike_in_editor_and_export(page, server):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Parity refs", "template": "blank"}).json()["id"]
    for block in ({"type": "heading", "content": {"text": "Notes", "level": 1}},
                  {"type": "references", "content": {"title": "References"}},   # no citations: a hint line
                  {"type": "heading", "content": {"text": "", "level": 2}},      # empty heading
                  {"type": "text", "content": {"markdown": ""}},                 # empty paragraph
                  {"type": "callout", "content": {"markdown": ""}}):             # empty callout
        assert c.post(f"/api/docs/{did}/blocks", json=block).status_code == 200
    for i in range(60):
        c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": f"Line {i + 1} of the notes."}})

    editor = _layout(page, f"{server}/d/{did}", ".cd-page")
    printed = _layout(page, f"{server}/print/{did}", ".cd-page")
    assert editor == printed  # every block on the same page, at the same offset and height
    pdf = c.get(f"/api/docs/{did}/export", params={"format": "pdf"})
    assert pdf.status_code == 200
    assert len(pypdf.PdfReader(io.BytesIO(pdf.content)).pages) == page.locator(".cd-page").count()
