"""Rendering defects found by the first evaluation with a strong model, in a real browser.

Captions were cut off after one line, tables broke words in the middle ("Buildin g"), heading
variants ignored the document's heading settings, justified text had rivers, a deleted block
was gone for good, and opening a text block that held a table deleted the table. Every check
of how a page looks is made on the editor and on the export route, which must agree.
"""
from __future__ import annotations

import io
import json

import pypdf
from conftest import api

SHY = chr(0xAD)  # the soft hyphen
# A 2x1 px PNG, so an image block has something to load.
PIXEL = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAAEUlEQVR4nGP8z8Dwn4GBgQEADQQB/1q3"
         "1mQAAAAASUVORK5CYII=")
LONG_CAPTION = ("The refrigeration loop moves heat from outdoor air to indoor air. Variable-speed compressors and "
                "controls help modern units keep working at lower outdoor temperatures, though output and efficiency "
                "decline as temperatures fall.")
PROSE = ("Electrification appears operationally feasible for most of the network, but the risks are concentrated "
         "in route exceptions and depot power. Infrastructure characteristics predominate in the comparison, and "
         "the remaining uncertainty is considerable where independent measurements are unavailable. ")

LAYOUT_JS = """() => [...document.querySelectorAll('.cd-page [data-block-id]')].map(el => ({
  id: el.dataset.blockId, page: Number(el.closest('.cd-page').dataset.page),
  top: Math.round(parseFloat(el.style.top)), h: Math.round(parseFloat(el.style.height))}))"""


def _open(pg, url: str):
    # Wide enough that the editor shows the page at 100%: at other zoom levels the browser
    # lays the visible copy out again at the zoomed size, and a line can break elsewhere.
    pg.set_viewport_size({"width": 1900, "height": 1100})
    pg.goto(url)
    pg.wait_for_selector(".cd-page [data-block-id]")
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(1200)
    if "/d/" in url:
        assert pg.locator("[data-testid=zoom-label]").inner_text() == "100%"


def _layout(pg, url: str):
    _open(pg, url)
    return sorted(((b["id"], b["page"], b["top"], b["h"]) for b in pg.evaluate(LAYOUT_JS)), key=lambda b: (b[1], b[2], b[0]))


def _doc(c, title: str, **settings) -> str:
    return c.post("/api/docs", json={"title": title, "template": "blank", "settings": settings}).json()["id"]


def _add(c, did: str, type_: str, content: dict, **layout) -> dict:
    r = c.post(f"/api/docs/{did}/blocks", json={"type": type_, "content": content, "layout": layout or None})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------ captions
FIGURES_JS = """() => [...document.querySelectorAll('.cd-page [data-block-id]')].map(item => {
  const box = item.getBoundingClientRect();
  const parts = [...item.querySelectorAll('figcaption, .cd-caption, .cd-fig-head, .cd-fig-source')];
  return {id: item.dataset.blockId,
          parts: parts.map(p => ({cls: p.className || p.tagName, lines: Math.round(p.getBoundingClientRect().height),
                                  cut: p.scrollHeight > p.clientHeight + 1,
                                  outside: p.getBoundingClientRect().bottom > box.bottom + 1}))};
}).filter(f => f.parts.length)"""


def test_long_captions_wrap_and_nothing_is_cut_off(page, server):
    c = api(server)
    did = _doc(c, "Captions", columns=2)
    _add(c, did, "text", {"markdown": PROSE})
    flow = _add(c, did, "image", {"src": PIXEL, "caption": LONG_CAPTION, "label": "Figure 1",
                                  "title": "How a cold-climate heat pump gathers and upgrades heat from outdoor air in winter",
                                  "source": "Adapted by the author from public descriptions of heat pump operation "
                                            "and cold-climate specifications."}, variant="figure-box")
    fixed = _add(c, did, "image", {"src": PIXEL, "caption": LONG_CAPTION}, height=220)
    chart = _add(c, did, "chart", {"caption": LONG_CAPTION, "spec": {
        "data": {"values": [{"x": "A", "y": 3}, {"x": "B", "y": 5}]}, "mark": "bar",
        "encoding": {"x": {"field": "x", "type": "nominal"}, "y": {"field": "y", "type": "quantitative"}}}}, height=260)
    table = _add(c, did, "table", {"rows": [["Path", "Total"], ["Diesel", "$27.68m"]], "caption": LONG_CAPTION})
    _add(c, did, "text", {"markdown": PROSE * 2})

    for route in ("print", "d"):
        _open(page, f"{server}/{route}/{did}")
        figures = {f["id"]: f["parts"] for f in page.evaluate(FIGURES_JS)}
        assert set(figures) == {flow["id"], fixed["id"], chart["id"], table["id"]}
        for bid, parts in figures.items():
            for p in parts:
                assert not p["cut"] and not p["outside"], (route, bid, p)
        # In a narrow column each of these runs to two lines or more (one line is 22px at most).
        assert all(p["lines"] > 22 for parts in figures.values() for p in parts), figures
        # A fixed-height figure keeps its height: the picture or chart gives up room, not the page.
        heights = {b["id"]: b["h"] for b in page.evaluate(LAYOUT_JS)}
        assert heights[fixed["id"]] == 220 and heights[chart["id"]] == 260
        chart_h = page.locator(f".cd-page [data-block-id={chart['id']}] svg").first.evaluate("e => e.getBoundingClientRect().height")
        assert 60 < chart_h < 215, chart_h   # shorter than with a one-line caption (232), and still a chart

    assert _layout(page, f"{server}/d/{did}") == _layout(page, f"{server}/print/{did}")
    pdf = c.get(f"/api/docs/{did}/export", params={"format": "pdf"})
    assert pdf.status_code == 200
    text = " ".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(pdf.content)).pages)
    assert text.replace("\n", " ").count("decline as temperatures fall") == 4  # each caption complete in the PDF


def test_a_short_caption_takes_the_room_it_always_did(page, server):
    """One-line captions are the usual case: existing documents must not move by a pixel."""
    c = api(server)
    did = _doc(c, "Short captions")
    img = _add(c, did, "image", {"src": PIXEL, "caption": "A short caption.", "label": "Figure 1", "title": "Title",
                                 "source": "Source: example.org"}, height=200)
    _open(page, f"{server}/print/{did}")
    got = page.evaluate("""id => { const f = document.querySelector(`.cd-page [data-block-id=${id}]`);
      const h = s => f.querySelector(s).getBoundingClientRect().height;
      return [h('.cd-fig-head'), h('.cd-fig-source'), h('figcaption'), h('img')]; }""", img["id"])
    assert got == [18, 14, 22, 200 - 26 - 22 - 28]


# -------------------------------------------------------------------------------- tables
BROKEN_WORDS_JS = """sel => { const out = [];
  for (const cell of document.querySelectorAll(sel)) {
    for (const node of cell.childNodes) {
      if (node.nodeType !== 3) continue;
      const re = /[^\\s-]+-?/g; let m;   // a compound may break after its own hyphen
      while ((m = re.exec(node.data))) {
        const r = document.createRange(); r.setStart(node, m.index); r.setEnd(node, m.index + m[0].length);
        const tops = new Set([...r.getClientRects()].filter(x => x.width > 0).map(x => Math.round(x.top)));
        if (tops.size > 1) out.push(m[0].replaceAll(String.fromCharCode(0xad), '|'));
      }
    }
  }
  return out; }"""
STUDIES = [["Study", "Setting", "Scale", "Design", "Reported effect"],
           ["Konopacki et al. (1998)", "California commercial buildings", "Building", "Monitored retrofit",
            "Albedo rose from about 0.20 to 0.60; hot-afternoon roof surface temperature fell."],
           ["Li, Bou-Zeid, and Oppenheimer (2014)", "Baltimore-Washington heat-wave episode", "Metropolitan",
            "WRF-PUCM simulation", "Cool roofs reduced surface UHI more than near-surface UHI."]]


def test_a_table_keeps_its_words_whole(page, server):
    """The evaluation's table printed "Buildin g" and "Metropo litan": every column could
    shrink to one letter, so short columns were squeezed below their longest word."""
    c = api(server)
    did = _doc(c, "Tables", columns=1)
    t = _add(c, did, "table", {"rows": STUDIES, "header": True})
    md = "| " + " | ".join(STUDIES[0]) + " |\n|:--|:--|:-:|--:|:--|\n" + "\n".join("| " + " | ".join(r) + " |" for r in STUDIES[1:])
    txt = _add(c, did, "text", {"markdown": "Before the table.\n\n" + md})
    for route in ("print", "d"):
        _open(page, f"{server}/{route}/{did}")
        for bid in (t["id"], txt["id"]):
            sel = f".cd-page [data-block-id={bid}] table"
            assert page.evaluate(BROKEN_WORDS_JS, f"{sel} th, {sel} td") == [], (route, bid)
            assert page.locator(sel).get_attribute("data-fit") is None
            fits = page.evaluate("s => { const t = document.querySelector(s); return t.offsetWidth <= t.parentElement.clientWidth + 1; }", sel)
            assert fits, (route, bid)
    assert _layout(page, f"{server}/d/{did}") == _layout(page, f"{server}/print/{did}")


def test_a_table_inside_text_looks_like_a_table_block_and_keeps_its_alignment(page, server):
    c = api(server)
    did = _doc(c, "Table styles")
    block = _add(c, did, "table", {"rows": [["Item", "Cost"], ["Vans", "$1.2m"]], "header": True, "align": ["left", "right"]})
    text = _add(c, did, "text", {"markdown": "| Item | Cost | Share |\n|:--|--:|:-:|\n| Vans | $1.2m | 40% |"})
    _open(page, f"{server}/print/{did}")
    style = """([id, cell]) => { const s = getComputedStyle(document.querySelector(`.cd-page [data-block-id=${id}] ${cell}`));
      return [s.textAlign.replace('-webkit-', ''), s.borderBottomWidth, s.borderLeftWidth, s.fontWeight, s.fontSize]; }"""
    head_block, head_text = page.evaluate(style, [block["id"], "th"]), page.evaluate(style, [text["id"], "th"])
    assert head_text == head_block == ["left", "2px", "0px", "650", head_block[4]]
    cell_block, cell_text = page.evaluate(style, [block["id"], "td:nth-child(2)"]), page.evaluate(style, [text["id"], "td:nth-child(2)"])
    assert cell_text == cell_block and cell_block[:3] == ["right", "1px", "0px"]
    assert page.evaluate(style, [text["id"], "td:nth-child(3)"])[0] == "center"


def test_a_table_too_wide_for_its_column_breaks_words_at_syllables_not_anywhere(page, server):
    """Last resort: when whole words cannot fit, a word breaks at a syllable with a hyphen."""
    c = api(server)
    did = _doc(c, "Narrow table", columns=3)
    t = _add(c, did, "table", {"rows": STUDIES, "header": True})
    _add(c, did, "text", {"markdown": PROSE * 6})
    seen = {}
    for route in ("print", "d"):
        _open(page, f"{server}/{route}/{did}")
        sel = f".cd-page [data-block-id={t['id']}] table"
        fit = page.locator(sel).get_attribute("data-fit")
        assert fit in ("1", "2", "3"), fit
        assert page.evaluate("s => { const t = document.querySelector(s); return t.offsetWidth <= t.parentElement.clientWidth + 1; }", sel)
        seen[route] = (fit, page.evaluate(BROKEN_WORDS_JS, f"{sel} th, {sel} td"))
    assert seen["print"] == seen["d"]   # the editor and the export break the same words
    fit, broken = seen["print"]
    if fit != "3":   # levels 1 and 2 only ever break at a soft hyphen
        assert broken and all("|" in w for w in broken), broken
    assert _layout(page, f"{server}/d/{did}") == _layout(page, f"{server}/print/{did}")


# ---------------------------------------------------------------------- heading variants
HEADING_JS = """id => { const h = document.querySelector(`.cd-page [data-block-id=${id}] .cd-h`); const s = getComputedStyle(h);
  return {case: s.textTransform, size: parseFloat(s.fontSize), weight: s.fontWeight, font: s.fontFamily.split(',')[0].replaceAll('"', '').replaceAll("'", '')}; }"""


def test_section_and_rule_headings_follow_the_documents_own_heading_settings(page, server):
    c = api(server)
    did = _doc(c, "Variants", theme="classic")
    section = _add(c, did, "heading", {"text": "Decision question and scope", "level": 2}, variant="section")
    rule = _add(c, did, "heading", {"text": "Five-year assessment", "level": 3}, variant="rule")
    display = _add(c, did, "heading", {"text": "The title", "level": 1}, variant="display")
    label = _add(c, did, "heading", {"text": "By the numbers", "level": 2}, variant="label")
    plain = _add(c, did, "heading", {"text": "A plain heading", "level": 2})

    _open(page, f"{server}/print/{did}")
    before = {k: page.evaluate(HEADING_JS, b["id"]) for k, b in
              (("section", section), ("rule", rule), ("display", display), ("label", label), ("plain", plain))}
    # With nothing set by the document, the variants look as they always did.
    assert before["section"]["case"] == before["rule"]["case"] == "uppercase"
    assert before["section"]["weight"] == "700" and abs(before["section"]["size"] - 15 * 1.02) < 0.05
    assert before["section"]["font"] == "Roboto Condensed"

    style = {"heading_case": "none", "heading_scale": 1.3, "heading_weight": 500, "font_heading": "'Montserrat', sans-serif"}
    assert c.patch(f"/api/docs/{did}", json={"settings": {"style": style}}).status_code == 200
    for route in ("print", "d"):
        _open(page, f"{server}/{route}/{did}")
        after = {k: page.evaluate(HEADING_JS, b["id"]) for k, b in
                 (("section", section), ("rule", rule), ("display", display), ("label", label), ("plain", plain))}
        assert after["section"] == {"case": "none", "size": after["plain"]["size"], "weight": "500", "font": "Montserrat"}, route
        assert abs(after["section"]["size"] - 15 * 1.3 * 1.3) < 0.05      # a level-2 heading on the new scale
        assert after["rule"]["case"] == "none" and abs(after["rule"]["size"] - 15 * 1.3) < 0.05 and after["rule"]["font"] == "Montserrat"
        # A title and a small label are not section headings: they keep their own look
        # (a label is as bold as the headings are, as it always was).
        assert after["display"] == before["display"], route
        assert after["label"] == {**before["label"], "weight": "500"}, route
    assert _layout(page, f"{server}/d/{did}") == _layout(page, f"{server}/print/{did}")

    # The Block panel says what the chosen style does.
    page.goto(f"{server}/d/{did}")
    page.locator(f"[data-block-id={section['id']}]").click()
    page.click("[data-testid=tab-block]")
    assert "replaces that default" in page.locator("[data-testid=variant-note]").inner_text()


# ---------------------------------------------------------------------------- hyphenation
PARAS_JS = """() => [...document.querySelectorAll('.cd-page .cd-md p')].map(p => ({text: p.textContent,
  hyphens: getComputedStyle(p).hyphens, h: Math.round(p.getBoundingClientRect().height)}))"""


def test_justified_text_is_hyphenated_the_same_in_the_editor_and_the_export(page, server):
    c = api(server)
    did = _doc(c, "Justified", columns=3, style={"text_align": "justify"})
    for _ in range(4):
        _add(c, did, "text", {"markdown": PROSE * 2})
    editor = _layout(page, f"{server}/d/{did}")
    ed_paras = page.evaluate(PARAS_JS)
    printed = _layout(page, f"{server}/print/{did}")
    pr_paras = page.evaluate(PARAS_JS)
    assert editor == printed   # every block on the same page, at the same offset and height
    assert [(p["text"], p["hyphens"]) for p in ed_paras] == [(p["text"], p["hyphens"]) for p in pr_paras]
    # Codraft marks the break points itself; the browser is told to use only those.
    assert all(SHY in p["text"] and p["hyphens"] == "manual" for p in pr_paras)
    first = pr_paras[0]["text"]
    assert SHY.join(["op", "er", "a", "tionally"]) in first and SHY.join(["mea", "sure", "ments"]) in first
    assert "Electrification " in first and "Infrastructure " in first   # a word with a capital stays whole
    hyphenated_height = sum(p["h"] for p in pr_paras)

    # The stored text is untouched. In the PDF each break point is tagged as a soft hyphen
    # (ActualText), so a reader that honours the tag, as PDF viewers do, finds whole words.
    doc = c.get(f"/api/docs/{did}").json()
    assert SHY not in json.dumps(doc["blocks"], ensure_ascii=False)
    pdf = c.get(f"/api/docs/{did}/export", params={"format": "pdf"})
    import pypdfium2

    text = pypdfium2.PdfDocument(pdf.content)[0].get_textpage().get_text_range()
    assert SHY not in text and "feasible" in text and "network" in text and "concentrated" in text

    # Turned off in Design, the words go back to breaking only between words.
    assert c.patch(f"/api/docs/{did}", json={"settings": {"style": {"hyphens": "manual"}}}).status_code == 200
    _open(page, f"{server}/print/{did}")
    off = page.evaluate(PARAS_JS)
    assert all(SHY not in p["text"] for p in off)
    assert sum(p["h"] for p in off) >= hyphenated_height
    assert _layout(page, f"{server}/d/{did}") == _layout(page, f"{server}/print/{did}")


def test_left_aligned_text_is_not_hyphenated_unless_asked(page, server):
    c = api(server)
    did = _doc(c, "Ragged", columns=3)
    _add(c, did, "text", {"markdown": PROSE * 2})
    _open(page, f"{server}/print/{did}")
    assert all(SHY not in p["text"] for p in page.evaluate(PARAS_JS))
    page.goto(f"{server}/d/{did}")
    page.click("[data-testid=tab-design]")
    assert page.locator("[data-testid=hyphens-select]").input_value() == "manual"
    page.select_option("[data-testid=hyphens-select]", "auto")
    page.wait_for_function(f"fetch('/api/docs/{did}').then(r => r.json()).then(d => d.settings.style.hyphens === 'auto')")
    _open(page, f"{server}/print/{did}")
    assert all(SHY in p["text"] for p in page.evaluate(PARAS_JS))


# ------------------------------------------------------------------ superscript, subscript
def test_superscript_and_subscript_render_and_survive_an_edit(page, server):
    c = api(server)
    did = _doc(c, "Units")
    b = _add(c, did, "text", {"markdown": "Flux of 72 W m^-2^ day^-1^ and CO~2~ at about ~5 to ~10 percent."})
    _open(page, f"{server}/print/{did}")
    html = page.locator(f".cd-page [data-block-id={b['id']}] .cd-md").inner_html()
    assert "m<sup>-2</sup> day<sup>-1</sup> and CO<sub>2</sub>" in html and "~5 to ~10" in html and "<del>" not in html

    page.goto(f"{server}/d/{did}")
    page.locator(f"[data-block-id={b['id']}]").dblclick()
    page.wait_for_selector("[data-testid=text-editor] sup")   # the rich editor holds them
    page.keyboard.press("Control+End")
    page.keyboard.type(" Checked.")
    page.keyboard.press("Control+Enter")
    page.wait_for_function(f"fetch('/api/docs/{did}').then(r => r.json()).then(d => d.blocks[0].content.markdown.endsWith('Checked.'))")
    md = c.get(f"/api/docs/{did}").json()["blocks"][0]["content"]["markdown"]
    assert "m^-2^ day^-1^" in md and "CO~2~" in md, md


# ------------------------------------------------------- a block the rich editor can't hold
def test_a_text_block_with_a_table_opens_as_markdown_and_keeps_the_table(page, server):
    """The rich-text editor has no tables: opening and closing such a block used to save it
    back without its table."""
    c = api(server)
    did = _doc(c, "Tables in text")
    md = "Costs by path:\n\n| Path | Total |\n|:--|--:|\n| Diesel | $27.68m |\n\nThat is all."
    b = _add(c, did, "text", {"markdown": md})
    page.goto(f"{server}/d/{did}")
    block = page.locator(f"[data-block-id={b['id']}]")
    block.dblclick()
    page.wait_for_selector("[data-testid=markdown-editor]")
    assert page.locator("[data-testid=text-editor]").count() == 0
    assert page.locator("[data-testid=markdown-editor]").input_value() == md
    page.keyboard.press("Control+Enter")                      # close without a change
    page.wait_for_selector("[data-testid=markdown-editor]", state="detached")
    got = c.get(f"/api/docs/{did}").json()["blocks"][0]
    assert got["content"]["markdown"] == md and got["n_versions"] == 1

    block.dblclick()
    page.wait_for_selector("[data-testid=markdown-editor]")
    page.fill("[data-testid=markdown-editor]", md.replace("$27.68m", "$27.70m"))
    page.keyboard.press("Control+Enter")
    page.wait_for_function(f"fetch('/api/docs/{did}').then(r => r.json()).then(d => d.blocks[0].content.markdown.includes('$27.70m'))")
    assert "| Path | Total |" in c.get(f"/api/docs/{did}").json()["blocks"][0]["content"]["markdown"]
    # The server has the new text; the page re-renders the block a moment later.
    cell = page.locator(f"[data-block-id={b['id']}] table td", has_text="$27.70m")
    cell.wait_for()
    assert cell.count() == 1


# ------------------------------------------------------------------ restoring a deleted block
def test_a_deleted_block_comes_back_from_the_toast_or_the_document_panel(page, server):
    c = api(server)
    did = _doc(c, "Undo")
    a = _add(c, did, "heading", {"text": "Keep", "level": 2})
    b = _add(c, did, "text", {"markdown": "The paragraph that gets deleted."})
    z = _add(c, did, "text", {"markdown": "Last."})
    page.goto(f"{server}/d/{did}")
    ids = lambda: [x["id"] for x in c.get(f"/api/docs/{did}").json()["blocks"]]  # noqa: E731

    page.locator(f"[data-block-id={b['id']}]").click()
    page.click("[data-testid=tab-block]")
    page.click("[data-testid=delete-block]")
    page.wait_for_selector("[data-testid=toast-action]")
    assert ids() == [a["id"], z["id"]]
    page.click("[data-testid=toast-action]")                   # Undo
    page.wait_for_selector(f"[data-block-id={b['id']}]")
    assert ids() == [a["id"], b["id"], z["id"]]                # back in its place

    page.locator(f"[data-block-id={b['id']}]").click()
    page.click("[data-testid=tab-block]")
    page.click("[data-testid=delete-block]")
    page.wait_for_selector(f"[data-block-id={b['id']}]", state="detached")
    page.click("[data-testid=tab-document]")
    row = page.locator("[data-testid=deleted-block]")
    row.wait_for()
    assert "The paragraph that gets deleted." in row.inner_text()
    row.locator("[data-testid=restore-block]").click()
    page.wait_for_selector(f"[data-block-id={b['id']}]")
    assert ids() == [a["id"], b["id"], z["id"]]
    page.wait_for_selector("[data-testid=deleted-blocks]", state="detached")   # nothing left to restore


# ----------------------------------------------------------------------------- chat panel
def test_a_long_run_of_tool_calls_folds_into_one_readable_line(page, server):
    c = api(server)
    did = _doc(c, "Chat")
    head = _add(c, did, "heading", {"text": "Executive summary", "level": 2})
    body = _add(c, did, "text", {"markdown": "A phased electrification of about 80 vans a year is the strongest option."})
    calls = ([{"id": f"c{i}", "name": "set_block_layout", "args": {"block_id": head["id"], "variant": "rule"}} for i in range(9)]
             + [{"id": "c9", "name": "propose_edit", "args": {"block_id": body["id"], "content": {"markdown": "x"}}},
                {"id": "c10", "name": "view_pages", "args": {"pages": [1]}},
                {"id": "c11", "name": "propose_delete", "args": {"block_id": "blk_gone000000"}}])
    history = ([{"role": "user", "content": "Restyle the headings."},
                {"role": "assistant", "content": "", "tool_calls": calls}]
               + [{"role": "tool", "tool_call_id": k["id"], "content": "ok"} for k in calls]
               + [{"role": "assistant", "content": "Done: the headings have a rule above them."},
                  {"role": "user", "content": "And the outline?"},
                  {"role": "assistant", "content": "Here it is.", "tool_calls": [{"id": "d0", "name": "get_document", "args": {}}]},
                  {"role": "tool", "tool_call_id": "d0", "content": "outline"}])
    page.route("**/api/docs/*/messages", lambda route: route.fulfill(json=history))
    page.goto(f"{server}/d/{did}")
    summary = page.locator("[data-testid=tool-summary]")
    summary.wait_for()
    assert summary.count() == 1
    assert summary.inner_text().strip() == ("12 steps: changed a block's layout ×9, suggested an edit, looked at the pages "
                                            "and 1 more kind")
    # Folded: only the short second turn's single call is listed.
    cards = page.locator("[data-testid=tool-card]")
    assert cards.count() == 1 and "Read the outline" in cards.first.inner_text()

    summary.locator("button").first.click()
    assert cards.count() == 13
    about = page.locator("[data-testid=tool-about]").all_inner_texts()
    assert about[0] == "heading “Executive summary”"                      # a block's words, not its id
    assert about[9].startswith("text “A phased electrification")
    assert about[10] == "page 1" and about[11] == "a block that is no longer there"
    assert head["id"] not in page.locator("[data-testid=chat-log]").inner_text()
    cards.first.click()                                                    # the raw arguments are a click away
    assert head["id"] in page.locator("[data-testid=chat-log]").inner_text()
