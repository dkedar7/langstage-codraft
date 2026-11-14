"""Layout building blocks in a real browser (gh #52): a row of cells, number tiles and a band.

Each is one atomic block on the page. The checks that matter are the parity gate's: the editor
and the export draw the same pixels and place every block at the same offset.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from conftest import api, page_image

LOREM = ("Agents increasingly produce documents rather than chat replies, yet most hosts still treat "
         "their output as a transcript. A document should be co-authored: the person owns the argument "
         "and the agent owns the presentation. ")
ORANGE = {"style": {"color_accent": "#ff5a1f"}}
CELLS = [{"markdown": "### Reliable\n\nRetries with backoff for up to 72 hours, with signed payloads."},
         {"markdown": "### Observable\n\nA delivery log customers can search, and replay of any event from the last 30 days."},
         {"markdown": "### Fair\n\nPer-customer rate limits keep one noisy tenant from starving the rest."}]
TILES = [{"value": "99.99%", "label": "delivered within 60 seconds"},
         {"value": "2.1 billion", "label": "events delivered a month"},
         {"value": "38 ms", "label": "median added latency"}]

LAYOUT_JS = """() => [...document.querySelectorAll('.cd-page [data-block-id]')].map(el => ({
  id: el.dataset.blockId, page: Number(el.closest('.cd-page').dataset.page),
  top: Math.round(parseFloat(el.style.top)), h: Math.round(parseFloat(el.style.height))}))"""


def _doc(c, title: str, **settings) -> str:
    return c.post("/api/docs", json={"title": title, "template": "blank", "settings": settings}).json()["id"]


def _add(c, did: str, type_: str, content: dict, **layout) -> dict:
    r = c.post(f"/api/docs/{did}/blocks", json={"type": type_, "content": content, "layout": layout or None})
    assert r.status_code == 200, r.text
    return r.json()


def _open(pg, url: str):
    pg.set_viewport_size({"width": 1900, "height": 1100})   # wide enough for the editor to show 100%
    pg.goto(url)
    pg.wait_for_selector(".cd-page [data-block-id]")
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(1200)
    if "/d/" in url:
        assert pg.locator("[data-testid=zoom-label]").inner_text() == "100%"


def _layout(pg, url: str):
    _open(pg, url)
    return sorted(((b["id"], b["page"], b["top"], b["h"]) for b in pg.evaluate(LAYOUT_JS)), key=lambda b: (b[1], b[2], b[0]))


def _editor_page(browser, server, did, n=1):
    ctx = browser.new_context(viewport={"width": 1800, "height": 1400}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(f"{server}/d/{did}")
    pg.wait_for_selector(f".cd-page[data-page='{n}']")
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(1500)
    while pg.locator("text=100%").count() == 0:
        label = pg.locator(".w-10.text-center").inner_text()
        pg.click("[title='Zoom in']" if int(label.rstrip('%')) < 100 else "[title='Zoom out']")
    pg.wait_for_timeout(500)
    pg.mouse.move(0, 0)
    img = page_image(pg, f".cd-page[data-page='{n}']")
    ctx.close()
    return img


def _exported_page(c, did, n=1):
    from PIL import Image

    exp = c.get(f"/api/docs/{did}/export", params={"format": "png"})
    assert exp.status_code == 200, exp.text
    if exp.headers["content-type"] == "application/zip":
        z = zipfile.ZipFile(io.BytesIO(exp.content))
        return Image.open(io.BytesIO(z.read(sorted(z.namelist())[n - 1]))).convert("RGB")
    return Image.open(io.BytesIO(exp.content)).convert("RGB")


def _assert_same_pixels(browser, server, c, did, what: str):
    from PIL import ImageChops

    exported = _exported_page(c, did)
    editor = _editor_page(browser, server, did)
    assert editor.size == exported.size, (what, editor.size, exported.size)
    diff = ImageChops.difference(editor, exported).convert("L")
    ratio = sum(1 for v in diff.tobytes() if v > 48) / (diff.size[0] * diff.size[1])
    assert ratio < 0.01, f"{what}: {ratio:.4%} of pixels differ between editor and export"
    # and it is not a blank page that trivially matches
    assert len(set(exported.resize((64, 64)).tobytes())) > 8, what


def _row_doc(c):
    did = _doc(c, "Parity: row", **ORANGE)
    _add(c, did, "text", {"markdown": LOREM})
    _add(c, did, "row", {"cells": CELLS}, span="full", variant="cards")
    _add(c, did, "row", {"cells": [{"markdown": "**1. Send** your event."}, {"markdown": "**2. Relay** signs and delivers it."},
                                   {"markdown": "**3. See** every attempt."},
                                   {"markdown": "**4. Replay** anything from the last 30 days.\n\n| a | b |\n|---|---|\n| 1 | 2 |"}],
                         "widths": [1, 1, 1, 1.6], "gap": 5}, span="full")
    _add(c, did, "row", {"cells": CELLS[:2]}, span="full", fill="#1d3557")
    _add(c, did, "text", {"markdown": LOREM * 2})
    return did


def _stat_doc(c):
    did = _doc(c, "Parity: tiles", columns=2, **ORANGE)
    _add(c, did, "text", {"markdown": LOREM}, span="full")
    _add(c, did, "stat", {"items": TILES}, span="full")
    _add(c, did, "stat", {"items": TILES[:2]}, variant="plain")              # one column of a two-column page
    _add(c, did, "stat", {"items": [{"value": "6 weeks", "label": "of engineering replaced"},
                                    {"value": "1", "label": "on-call rotation"}]}, fill="#1d3557")
    _add(c, did, "stat", {"items": TILES + TILES}, span="full", fill="accent")   # six tiles
    _add(c, did, "text", {"markdown": LOREM * 2}, span="full")
    return did


def _band_doc(c):
    did = _doc(c, "Parity: bands", columns=2, **ORANGE)
    _add(c, did, "heading", {"text": "Acme Relay", "level": 1}, span="full", variant="band")
    _add(c, did, "text", {"markdown": LOREM}, span="full", variant="lead")
    _add(c, did, "heading", {"text": "A section band in one column", "level": 2}, variant="band", fill="#1d3557")
    _add(c, did, "text", {"markdown": LOREM * 2})
    _add(c, did, "heading", {"text": "A light band", "level": 3}, span="full", fill="callout")
    _add(c, did, "callout", {"markdown": "A filled callout keeps its **left-aligned** text.", "tone": "info"}, span="full", fill="muted")
    _add(c, did, "callout", {"markdown": "**Start a 14-day pilot.** Write to relay@example.org.", "tone": "info"},
         span="full", variant="band")
    return did


@pytest.mark.parametrize("build", [_row_doc, _stat_doc, _band_doc], ids=["row", "stat", "band"])
def test_each_layout_block_exports_pixel_for_pixel(browser, server, build):
    """The parity gate, for each new block: the editor page and the exported PNG are one render."""
    c = api(server)
    _assert_same_pixels(browser, server, c, build(c), build.__name__)


def test_a_designed_one_pager_exports_pixel_for_pixel_and_places_every_block_alike(browser, page, server):
    """What the evaluation's one-pager needed, built from flowing blocks: a header band, value
    props side by side, a row of numbers, and a call-to-action box."""
    c = api(server)
    did = _doc(c, "Acme Relay", **ORANGE)
    _add(c, did, "heading", {"text": "Acme Relay", "level": 1}, span="full", variant="band")
    _add(c, did, "text", {"markdown": "Webhooks that arrive. Retries, signing and replay."}, span="full", variant="lead")
    _add(c, did, "row", {"cells": CELLS}, span="full", variant="cards")
    _add(c, did, "stat", {"items": TILES}, span="full")
    _add(c, did, "callout", {"markdown": "**Start a 14-day pilot.** Write to relay@example.org."}, span="full", variant="band")
    editor = _layout(page, f"{server}/d/{did}")
    printed = _layout(page, f"{server}/print/{did}")
    assert editor == printed and len(editor) == 5 and {b[1] for b in editor} == {1}   # one page, same offsets
    _assert_same_pixels(browser, server, c, did, "one-pager")
    # the look itself: white on the brand orange, tiles in one row, cells side by side
    look = page.evaluate("""() => {
      const css = (sel, p) => getComputedStyle(document.querySelector(sel))[p];
      const xs = (sel) => [...document.querySelectorAll(sel)].map(e => Math.round(e.getBoundingClientRect().top));
      return {band: css('.cd-page .cd-type-heading.cd-v-band .cd-h', 'backgroundColor'),
              bandText: css('.cd-page .cd-type-heading.cd-v-band .cd-h', 'color'),
              cta: css('.cd-page .cd-type-callout.cd-v-band .cd-callout', 'backgroundColor'),
              ctaAlign: css('.cd-page .cd-type-callout.cd-v-band .cd-md p', 'textAlign'),
              cells: xs('.cd-page .cd-type-row .cd-row-cell'), tiles: xs('.cd-page .cd-type-stat .cd-stat-tile')}; }""")
    assert look["band"] == look["cta"] == "rgb(255, 90, 31)" and look["bandText"] == "rgb(255, 255, 255)"
    assert look["ctaAlign"] == "center"
    assert len(look["cells"]) == 3 and len(set(look["cells"])) == 1      # three cells on one line
    assert len(look["tiles"]) == 3 and len(set(look["tiles"])) == 1


def test_empty_rows_and_tiles_paginate_alike_in_editor_and_export(page, server):
    """The editor shows hints in empty cells and tiles; the export must measure the same boxes."""
    c = api(server)
    did = _doc(c, "Parity: empty layout blocks")
    _add(c, did, "row", {"cells": [{"markdown": ""}, {"markdown": ""}, {"markdown": ""}]}, span="full")
    _add(c, did, "stat", {"items": [{"value": "", "label": ""}, {"value": "12", "label": ""}, {"value": "", "label": "label only"}]})
    _add(c, did, "row", {"cells": [{"markdown": "left"}, {"markdown": ""}]}, variant="cards")
    for i in range(55):
        _add(c, did, "text", {"markdown": f"Line {i + 1} of the notes."})
    editor = _layout(page, f"{server}/d/{did}")
    assert "Column 1…" in page.locator(".cd-page .cd-type-row").first.inner_text()      # the editor's hint
    printed = _layout(page, f"{server}/print/{did}")
    assert editor == printed
    assert page.locator(".cd-page").count() >= 2


def test_a_row_too_tall_for_a_page_is_placed_and_flagged(page, server):
    c = api(server)
    did = _doc(c, "Tall row")
    _add(c, did, "text", {"markdown": "Before."})
    tall = _add(c, did, "row", {"cells": [{"markdown": "\n\n".join([LOREM] * 14)}, {"markdown": "Short."}]}, span="full")
    _add(c, did, "text", {"markdown": "After."})
    _open(page, f"{server}/d/{did}")
    item = page.locator(f".ed-item[data-block-id={tall['id']}]")
    assert "overflow" in (item.get_attribute("class") or "")           # flagged like any oversized atomic block
    assert item.locator("text=overflow").count() == 1
    assert page.locator(f".cd-page [data-block-id={tall['id']}]").count() == 1   # one piece: a row never splits
    editor = sorted((b["id"], b["page"], b["top"], b["h"]) for b in page.evaluate(LAYOUT_JS))
    assert editor == sorted(_layout(page, f"{server}/print/{did}"))


def test_a_row_in_a_narrow_column_stacks_its_cells(page, server):
    c = api(server)
    did = _doc(c, "Narrow", columns=3)
    b = _add(c, did, "row", {"cells": CELLS + [{"markdown": "Fourth."}]})        # four cells in one of three columns
    _add(c, did, "text", {"markdown": LOREM * 3})
    editor = _layout(page, f"{server}/d/{did}")
    lefts = page.evaluate(f"""() => [...document.querySelectorAll('.cd-page [data-block-id={b["id"]}] .cd-row-cell')]
        .map(e => Math.round(e.getBoundingClientRect().left))""")
    assert len(lefts) == 4 and len(set(lefts)) == 1                    # stacked, not four slivers
    assert editor == _layout(page, f"{server}/print/{did}")


def test_add_columns_and_tiles_from_the_menu_and_edit_them_in_the_panel(page, server):
    c = api(server)
    did = _doc(c, "Editing", **ORANGE)
    head = _add(c, did, "heading", {"text": "Acme Relay", "level": 1})
    page.set_viewport_size({"width": 1700, "height": 1000})
    page.goto(f"{server}/d/{did}")
    page.wait_for_selector(f"[data-block-id={head['id']}]")
    blocks = lambda: c.get(f"/api/docs/{did}").json()["blocks"]  # noqa: E731

    # Columns: three empty cells, full width, edited in the Block panel
    page.click("[data-testid=add-block]")
    page.click("[data-testid=add-row]")
    page.wait_for_selector(".cd-page .cd-type-row")
    row = next(b for b in blocks() if b["type"] == "row")
    assert len(row["content"]["cells"]) == 3 and row["layout"]["span"] == "full"
    area = page.locator("[data-testid=row-cell-0] textarea")
    area.fill("### Reliable\n\nRetries for 72 hours.")
    area.blur()
    page.wait_for_function("document.querySelector('.cd-page .cd-type-row .cd-row-cell h3')?.textContent === 'Reliable'")
    page.click("[data-testid=row-add]")
    page.wait_for_function("document.querySelectorAll('.cd-page .cd-type-row .cd-row-cell').length === 4")
    assert page.locator("[data-testid=row-add]").is_disabled()         # four is the most
    page.click("[data-testid=row-remove-3]")
    page.wait_for_function("document.querySelectorAll('.cd-page .cd-type-row .cd-row-cell').length === 3")
    page.fill("[data-testid=row-widths]", "2, 1, 1")
    page.locator("[data-testid=row-widths]").blur()
    page.wait_for_function("""() => { const c = document.querySelectorAll('.cd-page .cd-type-row .cd-row-cell');
        return c.length === 3 && c[0].getBoundingClientRect().width > c[1].getBoundingClientRect().width * 1.7; }""")
    page.select_option("[data-testid=variant-select]", "cards")
    page.wait_for_selector(".cd-page .cd-type-row.cd-v-cards")
    row = next(b for b in blocks() if b["type"] == "row")
    assert row["content"]["cells"][0]["markdown"].startswith("### Reliable") and row["content"]["widths"] == [2, 1, 1]
    assert row["n_versions"] >= 4                                       # every edit is a version, as for any block

    # Number tiles
    page.click("[data-testid=add-block]")
    page.click("[data-testid=add-stat]")
    page.wait_for_selector(".cd-page .cd-type-stat")
    page.fill("[data-testid=stat-value-0]", "99.99%")
    page.fill("[data-testid=stat-label-0]", "delivered within 60 seconds")   # still inside the tiles: nothing saved yet
    assert next(b for b in blocks() if b["type"] == "stat")["content"]["items"][0]["value"] == ""
    page.locator("[data-testid=stat-label-0]").blur()                         # leaving them saves both fields at once
    page.wait_for_function("document.querySelector('.cd-page .cd-stat-value')?.textContent === '99.99%'")
    page.select_option("[data-testid=fill-select]", "accent")
    page.wait_for_function("""() => getComputedStyle(document.querySelector('.cd-page .cd-stat-tile')).backgroundColor
        === 'rgb(255, 90, 31)'""")
    stat = next(b for b in blocks() if b["type"] == "stat")
    assert stat["content"]["items"][0] == {"value": "99.99%", "label": "delivered within 60 seconds"}
    assert stat["layout"]["fill"] == "accent"

    # A header band on the heading, in a colour of the author's own
    page.locator(f".cd-page [data-block-id={head['id']}]").click()
    page.click("[data-testid=tab-block]")
    page.select_option("[data-testid=variant-select]", "band")
    page.wait_for_function("""() => getComputedStyle(document.querySelector('.cd-page .cd-type-heading .cd-h')).backgroundColor
        === 'rgb(255, 90, 31)'""")
    assert "strip" in page.locator("[data-testid=variant-note]").inner_text()
    page.select_option("[data-testid=fill-select]", "custom")
    page.wait_for_selector("[data-testid=fill-color]")
    page.fill("[data-testid=fill-color]", "#1d3557")
    page.locator("[data-testid=fill-color]").blur()
    page.wait_for_function("""() => getComputedStyle(document.querySelector('.cd-page .cd-type-heading .cd-h')).backgroundColor
        === 'rgb(29, 53, 87)'""")
    lay = next(b for b in blocks() if b["id"] == head["id"])["layout"]
    assert lay["variant"] == "band" and lay["fill"] == "#1d3557"


def test_word_and_powerpoint_exports_of_the_new_blocks(server):
    import docx
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    c = api(server)
    did = _doc(c, "Exports", **ORANGE)
    _add(c, did, "heading", {"text": "Acme Relay", "level": 1}, span="full", variant="band")
    _add(c, did, "text", {"markdown": "A plain paragraph."})
    _add(c, did, "row", {"cells": CELLS}, span="full")
    _add(c, did, "stat", {"items": TILES}, span="full")
    word = c.get(f"/api/docs/{did}/export", params={"format": "docx"})
    assert word.status_code == 200, word.text
    d = docx.Document(io.BytesIO(word.content))
    assert [len(t.columns) for t in d.tables] == [3, 3]
    assert d.tables[0].rows[0].cells[0].text.startswith("Reliable") and d.tables[1].rows[0].cells[0].text.startswith("99.99%")
    deck = c.get(f"/api/docs/{did}/export", params={"format": "pptx"})
    assert deck.status_code == 200, deck.text
    shapes = list(Presentation(io.BytesIO(deck.content)).slides[0].shapes)
    kinds = [s.shape_type for s in shapes]
    # the band, the row and the tiles are pictures of themselves (a text box can't draw them);
    # the plain paragraph stays an editable text box
    assert kinds.count(MSO_SHAPE_TYPE.PICTURE) == 3 and kinds.count(MSO_SHAPE_TYPE.TEXT_BOX) == 1
    assert shapes[kinds.index(MSO_SHAPE_TYPE.TEXT_BOX)].text_frame.text == "A plain paragraph."


def test_a_row_with_a_fixed_height_too_small_for_its_cells_is_flagged(page, server):
    c = api(server)
    did = _doc(c, "Cut off")
    ok = _add(c, did, "row", {"cells": CELLS}, span="full", height=400)
    cut = _add(c, did, "row", {"cells": CELLS}, span="full", height=40)      # the cells need more than 40px
    _open(page, f"{server}/d/{did}")
    assert "overflow" not in (page.locator(f".ed-item[data-block-id={ok['id']}]").get_attribute("class") or "")
    assert "overflow" in (page.locator(f".ed-item[data-block-id={cut['id']}]").get_attribute("class") or "")
