"""0.9.0 in a real browser: tables that split and repeat their header, text that back-fills the
gap before a figure, a block anchored to the bottom of its page, a pinned band that fills its
box, Markdown in table cells, numbered citations and the reference list.

Every one is checked the way the parity gate checks a page: the editor and the export place
each piece at the same offset and draw the same pixels.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from conftest import api, page_image

LOREM = ("Agents increasingly produce documents rather than chat replies, yet most hosts still treat "
         "their output as a transcript. A document should be co-authored: the person owns the argument "
         "and the agent owns the presentation. ")
BAR = {"data": {"values": [{"a": "North", "b": 28}, {"a": "South", "b": 55}, {"a": "East", "b": 43}]},
       "mark": "bar", "encoding": {"x": {"field": "a", "type": "nominal"}, "y": {"field": "b", "type": "quantitative"}}}

PIECES_JS = """() => [...document.querySelectorAll('.cd-page [data-block-id]')].map(el => ({
  id: el.dataset.blockId, page: Number(el.closest('.cd-page').dataset.page),
  top: Math.round(parseFloat(el.style.top)), h: Math.round(parseFloat(el.style.height))}))"""


def _doc(c, title: str, **settings) -> str:
    return c.post("/api/docs", json={"title": title, "template": "blank", "settings": settings}).json()["id"]


def _add(c, did: str, type_: str, content: dict, refs: list | None = None, **layout) -> dict:
    r = c.post(f"/api/docs/{did}/blocks", json={"type": type_, "content": content, "layout": layout or None, "refs": refs})
    assert r.status_code == 200, r.text
    return r.json()


def _open(pg, url: str):
    pg.set_viewport_size({"width": 1900, "height": 1100})   # wide enough for the editor to show 100%
    pg.goto(url)
    pg.wait_for_selector(".cd-page [data-block-id]")
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(1500)
    if "/d/" in url:
        assert pg.locator("[data-testid=zoom-label]").inner_text() == "100%"


def _pieces(pg, url: str):
    _open(pg, url)
    return sorted(((b["id"], b["page"], b["top"], b["h"]) for b in pg.evaluate(PIECES_JS)), key=lambda b: (b[1], b[2], b[0]))


def _same_layout(page, server, did):
    """Every piece of every block at the same page and offset in the editor and the export."""
    editor = _pieces(page, f"{server}/d/{did}")
    printed = _pieces(page, f"{server}/print/{did}")
    assert editor == printed, (editor, printed)
    return printed


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


def _exported_pages(c, did):
    from PIL import Image

    exp = c.get(f"/api/docs/{did}/export", params={"format": "png"})
    assert exp.status_code == 200, exp.text
    if exp.headers["content-type"] == "application/zip":
        z = zipfile.ZipFile(io.BytesIO(exp.content))
        return [Image.open(io.BytesIO(z.read(n))).convert("RGB") for n in sorted(z.namelist())]
    return [Image.open(io.BytesIO(exp.content)).convert("RGB")]


def _assert_same_pixels(browser, server, c, did, what: str, pages=(1,)):
    from PIL import ImageChops

    exported = _exported_pages(c, did)
    for n in pages:
        editor = _editor_page(browser, server, did, n)
        assert editor.size == exported[n - 1].size, (what, n, editor.size, exported[n - 1].size)
        diff = ImageChops.difference(editor, exported[n - 1]).convert("L")
        ratio = sum(1 for v in diff.tobytes() if v > 48) / (diff.size[0] * diff.size[1])
        assert ratio < 0.01, f"{what} page {n}: {ratio:.4%} of pixels differ between editor and export"
        assert len(set(exported[n - 1].resize((64, 64)).tobytes())) > 8, what   # not a blank page that trivially matches
    return exported


# ------------------------------------------------------------------------------ tables
def _table_rows(n: int) -> list[list[str]]:
    return [["Line", "Diesel", "Electric", "Notes"]] + [
        [f"Cost line {i}", f"${i * 3 + 1}.{i % 10}0m", f"${i * 2 + 2}.{(i * 7) % 10}0m",
         "A note long enough to wrap onto a second line in a narrow column." if i % 4 == 0 else "Quoted"]
        for i in range(1, n + 1)]


def test_a_long_table_splits_between_rows_and_repeats_its_header(browser, page, server):
    c = api(server)
    did = _doc(c, "Parity: a table across pages")
    _add(c, did, "text", {"markdown": LOREM * 3})
    t = _add(c, did, "table", {"rows": _table_rows(24), "caption": "Table 1. Five-year cost lines."}, variant="figure-box")
    after = _add(c, did, "text", {"markdown": LOREM * 2})
    pieces = _same_layout(page, server, did)
    mine = [p for p in pieces if p[0] == t["id"]]
    assert [p[1] for p in mine] == [1, 2], mine                 # it used to move whole to page 2 and overflow it
    first, second = mine
    content_h = 1056 - 2 * round(20 * 96 / 25.4)
    assert first[2] + first[3] > content_h - 60                 # the first piece runs to the foot of page 1
    assert second[2] == 0                                       # and the rest opens page 2
    assert [p[1] for p in pieces if p[0] == after["id"]] == [2]
    # The continued piece draws the header row again, above rows the first piece didn't show.
    _open(page, f"{server}/print/{did}")
    seen = page.evaluate("""(id) => [...document.querySelectorAll(`.cd-item[data-block-id="${id}"]`)].map((item) => {
      const box = item.getBoundingClientRect();
      const shown = (el) => { const r = el.getBoundingClientRect(); return r.top >= box.top - 1 && r.bottom <= box.bottom + 1 && r.height > 0; };
      const head = item.querySelector('.cd-item-head');
      return { head: head ? [...head.querySelectorAll('th')].filter(shown).map((e) => e.innerText.replace(/\u00ad/g, '')) : null,
               ownHead: [...item.querySelectorAll('th')].filter((e) => !e.closest('.cd-item-head') && shown(e)).length,
               rows: [...item.querySelectorAll('tbody tr')].filter((e) => !e.closest('.cd-item-head') && shown(e)).map((e) => e.cells[0].innerText),
               caption: [...item.querySelectorAll('.cd-caption')].filter((e) => !e.closest('.cd-item-head') && shown(e)).length }; })""", t["id"])
    assert seen[0]["head"] is None and seen[0]["ownHead"] == 4          # the first piece shows the real header
    assert seen[1]["head"] == ["Line", "Diesel", "Electric", "Notes"]   # the second repeats it
    assert seen[1]["ownHead"] == 0
    rows = seen[0]["rows"] + seen[1]["rows"]
    assert rows == [f"Cost line {i}" for i in range(1, 25)]             # every row once, none lost at the cut
    assert len(seen[0]["rows"]) >= 2 and len(seen[1]["rows"]) >= 2      # never a lone header or a single row
    assert seen[0]["caption"] == 0 and seen[1]["caption"] == 1          # the caption stays with the last rows
    _assert_same_pixels(browser, server, c, did, "table split", pages=(1, 2))


def test_a_table_in_two_columns_continues_in_the_next_column_under_its_header(page, server):
    c = api(server)
    did = _doc(c, "Parity: a table across columns", columns=2)
    t = _add(c, did, "table", {"rows": [r[:2] for r in _table_rows(60)]})
    _add(c, did, "text", {"markdown": LOREM})
    pieces = [p for p in _same_layout(page, server, did) if p[0] == t["id"]]
    assert len(pieces) >= 2 and pieces[0][1] == pieces[1][1] == 1       # two pieces side by side on page 1
    assert page.locator(f".cd-page[data-page='1'] .cd-item[data-block-id='{t['id']}'] .cd-item-head").count() == 1


def test_a_short_table_and_a_fixed_height_table_stay_whole(page, server):
    c = api(server)
    did = _doc(c, "Tables that don't split")
    _add(c, did, "text", {"markdown": LOREM * 9})
    small = _add(c, did, "table", {"rows": _table_rows(3)})                    # 3 rows: no split keeps 2 + 2
    fixed = _add(c, did, "table", {"rows": _table_rows(30)}, height=300)       # a fixed height is a fixed box
    pieces = _same_layout(page, server, did)
    assert len([p for p in pieces if p[0] == small["id"]]) == 1
    assert len([p for p in pieces if p[0] == fixed["id"]]) == 1
    assert page.locator(".cd-item-head").count() == 0


def test_table_cells_render_markdown_and_a_total_row(browser, page, server):
    c = api(server)
    did = _doc(c, "Parity: table cells")
    _add(c, did, "table", {"rows": [["Line", "Cost", "Source"], ["Vans", "$24.40m", "[docs](https://example.org)"],
                                    ["Energy, m^2^ and CO~2~", "`kWh`", "*estimate*"], ["**Total**", "**$27.68m**", ""]],
                           "total": True, "align": ["left", "right", "left"]})
    _add(c, did, "text", {"markdown": LOREM})
    _same_layout(page, server, did)
    got = page.evaluate("""() => { const t = document.querySelector('.cd-page table.cd-table');
      const css = (el, p) => getComputedStyle(el)[p];
      return { strong: [...t.querySelectorAll('td strong')].map((e) => e.innerText), em: t.querySelector('td em').innerText.replace(/\u00ad/g, ''),
               code: t.querySelector('td code').innerText, link: t.querySelector('td a').getAttribute('href'),
               sup: t.querySelector('td sup').innerText, sub: t.querySelector('td sub').innerText,
               raw: t.innerText.includes('**') || t.innerText.includes('`'),
               totalWeight: css(t.querySelector('tr.cd-total td:last-child'), 'fontWeight'),
               plainWeight: css(t.querySelectorAll('tbody tr')[0].cells[0], 'fontWeight') }; }""")
    assert got["strong"] == ["Total", "$27.68m"] and got["em"] == "estimate" and got["code"] == "kWh"
    assert got["link"] == "https://example.org" and got["sup"] == "2" and got["sub"] == "2" and not got["raw"]
    assert int(got["totalWeight"]) >= 600 > int(got["plainWeight"])
    _assert_same_pixels(browser, server, c, did, "table cells")


# ------------------------------------------------------------------------------ back-fill
def _gap_doc(c, backfill: bool):
    did = _doc(c, f"Parity: back-fill {'on' if backfill else 'off'}", backfill=backfill)
    _add(c, did, "text", {"markdown": LOREM * 8})
    _add(c, did, "heading", {"text": "Results", "level": 2})
    fig = _add(c, did, "chart", {"spec": {**BAR, "height": 420}, "caption": "Figure 1. Deliveries by region."})
    text = _add(c, did, "text", {"markdown": "\n\n".join([LOREM] * 5)})
    nxt = _add(c, did, "heading", {"text": "Discussion", "level": 2})
    _add(c, did, "text", {"markdown": LOREM})
    return did, fig, text, nxt


def test_text_back_fills_the_gap_before_a_figure_and_the_figure_opens_the_next_page(browser, page, server):
    c = api(server)
    did, fig, text, nxt = _gap_doc(c, True)
    pieces = _same_layout(page, server, did)
    at = lambda b: [(p[1], p[2]) for p in pieces if p[0] == b["id"]]  # noqa: E731
    assert at(fig) == [(2, 0)]                                  # the figure opens page 2
    assert [p for p, _ in at(text)] == [1, 2]                   # the text after it fills page 1, then continues
    assert at(text)[1][1] > 400                                 # ... below the figure
    assert at(nxt)[0][0] == 2                                   # the next section never moves ahead of the figure
    last = max(p[2] + p[3] for p in pieces if p[1] == 1)
    assert last > 1056 - 2 * round(20 * 96 / 25.4) - 120        # page 1 is full: no gap left (it was 40% empty)
    _assert_same_pixels(browser, server, c, did, "back-fill", pages=(1, 2))


def test_with_back_fill_off_the_figure_moves_whole_and_the_gap_stays(page, server):
    c = api(server)
    did, fig, text, _ = _gap_doc(c, False)
    pieces = _same_layout(page, server, did)
    assert [p[1] for p in pieces if p[0] == fig["id"]] == [2]
    assert min(p[1] for p in pieces if p[0] == text["id"]) == 2          # as before 0.9.0
    # switching it on in Document settings closes the gap
    _open(page, f"{server}/d/{did}")
    page.click("[data-testid=tab-document]")
    page.click("[data-testid=backfill]")       # it saves, then shows as checked
    page.wait_for_function("document.querySelector('[data-testid=backfill]').checked")
    page.wait_for_timeout(800)
    assert min(b["page"] for b in page.evaluate(PIECES_JS) if b["id"] == text["id"]) == 1
    assert c.get(f"/api/docs/{did}").json()["settings"]["backfill"] is True


# ------------------------------------------------------------------------------ bottom anchor
def test_a_block_anchored_to_the_bottom_sits_at_the_foot_of_the_page(browser, page, server):
    c = api(server)
    did = _doc(c, "Acme Relay", style={"color_accent": "#ff5a1f"})
    _add(c, did, "heading", {"text": "Acme Relay", "level": 1}, span="full", variant="band")
    _add(c, did, "text", {"markdown": LOREM}, span="full", variant="lead")
    cta = _add(c, did, "callout", {"markdown": "**Start a 14-day pilot.** Write to relay@example.org."},
               span="full", variant="band", anchor="bottom")
    later = _add(c, did, "text", {"markdown": LOREM * 2}, span="full")
    pieces = _same_layout(page, server, did)
    content_h = 1056 - 2 * (20 * 96 / 25.4)
    box = next(p for p in pieces if p[0] == cta["id"])
    assert box[1] == 1 and abs(box[2] + box[3] - content_h) <= 1          # flush with the bottom margin
    text = next(p for p in pieces if p[0] == later["id"])
    assert text[1] == 1 and text[2] + text[3] < box[2]                    # later text flows above it
    assert all(p[1] == 1 for p in pieces)
    _assert_same_pixels(browser, server, c, did, "bottom anchor")
    # The Block panel has the control, and it and the pin replace each other.
    _open(page, f"{server}/d/{did}")
    page.click(f".ed-item[data-block-id='{cta['id']}']")
    page.click("[data-testid=tab-block]")
    assert page.is_checked("[data-testid=anchor-bottom]")
    page.click("[data-testid=anchor-bottom]")
    page.wait_for_function("!document.querySelector('[data-testid=anchor-bottom]').checked")
    page.wait_for_timeout(600)
    moved = next(b for b in page.evaluate(PIECES_JS) if b["id"] == cta["id"])
    assert moved["top"] + moved["h"] < content_h - 100                    # back in the flow, under the lead
    saved = next(b for b in c.get(f"/api/docs/{did}").json()["blocks"] if b["id"] == cta["id"])
    assert saved["layout"]["anchor"] is None


def test_an_anchored_block_that_does_not_fit_goes_to_the_foot_of_the_next_page(page, server):
    c = api(server)
    did = _doc(c, "Anchor: next page")
    _add(c, did, "text", {"markdown": "\n\n".join([LOREM] * 12)})
    note = _add(c, did, "callout", {"markdown": "Signed: the operations team."}, anchor="bottom")
    pieces = _same_layout(page, server, did)
    box = next(p for p in pieces if p[0] == note["id"])
    content_h = 1056 - 2 * (20 * 96 / 25.4)
    assert abs(box[2] + box[3] - content_h) <= 1
    below = [p for p in pieces if p[1] == box[1] and p[0] != note["id"] and p[2] + p[3] > box[2]]
    assert not below                                                      # nothing overlaps it


# ------------------------------------------------------------------------------ pinned band, overflow
def test_a_pinned_band_fills_its_box_and_cut_off_content_is_flagged(browser, page, server):
    c = api(server)
    did = _doc(c, "Parity: pinned band", style={"color_accent": "#ff5a1f"})
    _add(c, did, "text", {"markdown": LOREM * 2})
    band = _add(c, did, "callout", {"markdown": "**Start a 14-day pilot.**\n\nContact relay@example.org."},
                variant="band", pin={"anchor": "page", "page": 1, "x": 68, "y": 812, "w": 680, "h": 176})
    tight = _add(c, did, "text", {"markdown": LOREM * 3}, pin={"anchor": "page", "page": 1, "x": 68, "y": 420, "w": 300, "h": 60})
    _same_layout(page, server, did)
    got = page.evaluate("""([band, tight]) => { const q = (id) => document.querySelector(`.cd-page .cd-item[data-block-id="${id}"]`);
      const fill = q(band).querySelector('.cd-callout').getBoundingClientRect();
      const text = q(band).querySelector('.cd-md').getBoundingClientRect();
      return { fillH: Math.round(fill.height), above: Math.round(text.top - fill.top), below: Math.round(fill.bottom - text.bottom),
               band: q(band).dataset.overflow, tight: q(tight).dataset.overflow }; }""", [band["id"], tight["id"]])
    assert got["fillH"] == 176                                   # it used to draw about 85 px tall at the top of the box
    assert abs(got["above"] - got["below"]) <= 2                 # and the text sits in the middle
    assert got["band"] == "0" and got["tight"] == "1"            # content taller than its box: flagged, not silent
    _open(page, f"{server}/d/{did}")
    assert page.locator(f".ed-item.overflow[data-block-id='{tight['id']}']").count() == 1
    assert page.locator(f".ed-item.overflow[data-block-id='{band['id']}']").count() == 0
    _assert_same_pixels(browser, server, c, did, "pinned band")


def test_the_agent_page_view_reports_the_cut_off_block(server):
    """render.screenshot_pages carries the flag view_pages turns into an OVERFLOW line."""
    from langstage_codraft.render import get_renderer

    c = api(server)
    did = _doc(c, "Overflow for the agent")
    ok = _add(c, did, "text", {"markdown": LOREM})
    tight = _add(c, did, "text", {"markdown": LOREM * 3}, pin={"page": 1, "top": 300, "column": 0, "span": 1, "height": 50})
    r = get_renderer()
    if r is None:
        pytest.skip("no renderer in this server")
    shots = r.call(r.screenshot_pages(did))
    assert shots[0]["overflow"] == [tight["id"]] and ok["id"] in shots[0]["blocks"]


# ------------------------------------------------------------------------------ references
RIVERA = {"title": "Fleet costs", "authors": "Rivera, A. and Chen, B.", "year": "2024", "publisher": "Example Press",
          "url": "https://example.org/fleet", "note": "PRIVATE: supports the cost table"}


def test_citations_are_numbered_and_the_list_prints_the_fields_not_the_note(browser, page, server):
    c = api(server)
    did = _doc(c, "Parity: references")
    _add(c, did, "text", {"markdown": "Charging is the constraint [@chen2023], and costs fell [@rivera2024; @chen2023]."},
         refs=[RIVERA, {"title": "Charging at depots", "authors": "Chen, B.", "year": "2023"}])
    _add(c, did, "table", {"rows": [["Claim", "Source"], ["Vans cost more up front", "[@rivera2024]"]]})
    _add(c, did, "text", {"markdown": "Uncited, but carried: the handbook."}, refs=[{"title": "Acme handbook", "note": "PRIVATE too"}])
    _add(c, did, "references", {})
    _same_layout(page, server, did)
    got = page.evaluate("""() => { const pg = document.querySelector('.cd-page');
      return { text: pg.querySelector('.cd-type-text .cd-md').innerText, cell: pg.querySelector('table.cd-table td:last-child').innerText,
               links: [...pg.querySelectorAll('.cd-type-text a.cd-cite')].map((a) => a.getAttribute('href')),
               list: [...pg.querySelectorAll('.cd-refs li')].map((li) => [li.id, li.innerText]) }; }""")
    assert got["text"] == "Charging is the constraint [1], and costs fell [2, 1]."
    assert got["cell"] == "[2]" and got["links"] == ["#cd-ref-1", "#cd-ref-2", "#cd-ref-1"]
    assert got["list"] == [["cd-ref-1", "Chen, B. (2023). Charging at depots."],
                           ["cd-ref-2", "Rivera, A. and Chen, B. (2024). Fleet costs. Example Press. https://example.org/fleet"],
                           ["cd-ref-3", "Acme handbook."]]
    assert "PRIVATE" not in page.locator(".cd-page").first.inner_text()
    _assert_same_pixels(browser, server, c, did, "references")
    # The Block panel edits the fields and shows the key to cite by.
    _open(page, f"{server}/d/{did}")
    page.click(".ed-item[data-testid=block-text] a.cd-cite >> nth=0")    # a click on a citation selects the block
    assert page.url.endswith(f"/d/{did}")                                 # ... and never leaves the editor
    page.click("[data-testid=tab-block]")
    assert page.locator("[data-testid=source-authors]").first.input_value() == "Rivera, A. and Chen, B."
    assert page.locator("[data-testid=source-cite]").first.inner_text() == "Cite it in the text as [@rivera2024]"
    page.locator("[data-testid=source-year]").first.fill("2025")
    page.click("text=Save sources")
    page.wait_for_timeout(600)
    assert "Rivera, A. and Chen, B. (2025). Fleet costs." in page.locator(".cd-page .cd-refs").inner_text()
