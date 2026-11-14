"""0.9.0, from the re-evaluation of 0.8.0: tables with Markdown cells and a total row, the
reference model and citations, a block anchored to the bottom of its page, reserved areas that
replace each other, back-fill before a figure, overflow reported to the agent, chart checks.

The paginator's own rules are unit tested in the frontend (layout/paginate.flow.test.ts) and
the editor/export parity of each in tests/e2e/test_flow_round2.py.
"""
from __future__ import annotations

import io
import json

import pytest

from langstage_codraft import exporters, refs
from langstage_codraft.charts import chart_warnings
from langstage_codraft.importers import from_markdown
from langstage_codraft.store import Invalid
from langstage_codraft.tools import (
    compile_references,
    get_document,
    page_shots,
    propose_edit,
    read_blocks,
    reserve_area,
    set_block_layout,
    set_document_style,
)


def call(tool, cfg, **args):
    return tool.invoke(args, config=cfg)


# ------------------------------------------------------------------------------ references
RIVERA = {"title": "Fleet costs", "authors": "Rivera, A. and Chen, B.", "year": "2024", "publisher": "Example Press",
          "url": "https://example.org/fleet", "accessed": "2026-01-05", "note": "supports the cost table; check 2025 edition"}


def test_a_reference_keeps_its_fields_and_gets_a_key(store):
    d = store.create_document("A")
    b = store.insert_block(d["id"], "text", {"markdown": "x"}, refs=[RIVERA, {"title": "Acme handbook"}, "Plain"])
    assert b["refs"][0] == {**RIVERA, "key": "rivera2024"}
    assert [r["key"] for r in b["refs"]] == ["rivera2024", "acmehandbook", "plain"]
    # an explicit key is kept; one that can't be cited is replaced; two in a block never collide
    b = store.edit_block(b["id"], refs=[{**RIVERA, "key": "@fleet"}, {"title": "T", "key": "not a key"},
                                         {"title": "Fleet costs again", "authors": "Rivera, A.", "year": "2024"},
                                         {"title": "Other", "key": "fleet"}])
    assert [r["key"] for r in b["refs"]] == ["fleet", "ref", "rivera2024", "fleet-2"]
    assert refs.clean_refs([{"authors": ["Ana Rivera", "Bo Chen"], "title": "T", "year": 2024}])[0]["authors"] == "Ana Rivera, Bo Chen"
    assert refs.clean_refs([{}, 3, {"url": ""}]) == []


def test_references_saved_by_an_older_version_read_back_in_the_new_shape(store):
    d = store.create_document("A")
    b = store.insert_block(d["id"], "text", {"markdown": "x"})
    old = json.dumps([{"title": "Old source", "url": "https://example.org/old", "note": "n"}])
    store._db.execute("UPDATE versions SET refs = ? WHERE id = ?", (old, b["head"]))
    store._db.commit()
    got = store.get_block(b["id"])["refs"]
    assert got == [{"key": "oldsource", "title": "Old source", "url": "https://example.org/old", "authors": "",
                    "year": "", "publisher": "", "accessed": "", "note": "n"}]
    # saving the same references again is not a change (no new version)
    assert store.edit_block(b["id"], refs=got)["head"] == b["head"]


def test_numbering_follows_first_appearance_and_matches_every_surface(store, cfg_for):
    d = store.create_document("A")
    chen = {"title": "Charging", "authors": "Chen, B.", "year": "2023"}
    one = store.insert_block(d["id"], "text", {"markdown": "Costs fell [@chen2023], as [@rivera2024] found."}, refs=[RIVERA, chen])
    store.insert_block(d["id"], "table", {"rows": [["Item", "Source"], ["Vans", "[@acme]"]]})
    store.insert_block(d["id"], "text", {"markdown": "Later."}, refs=[{"title": "Acme handbook", "key": "acme"},
                                                                    {**RIVERA, "key": "dup"}])
    blocks = store.list_blocks(d["id"])
    got = refs.compile_refs(blocks)
    assert [(r["n"], r["key"]) for r in got] == [(1, "chen2023"), (2, "rivera2024"), (3, "acme")]
    assert got[0]["block"] == one["id"]
    assert refs.numbers_for(blocks) == {"chen2023": 1, "rivera2024": 2, "acme": 3, "dup": 2}
    assert refs.replace_cites("x [@rivera2024; @acme] y [@nobody]", refs.numbers_for(blocks)) == "x [2, 3] y [?]"
    assert refs.cite_keys("`code` [@a] and [@b; @c], not [1] or [text](u)") == ["a", "b", "c"]
    out = call(compile_references, cfg_for(d["id"]))
    assert out.splitlines()[0].startswith("[1] Chen, B. (2023). Charging.  — cite as [@chen2023]")
    assert "private note: supports the cost table" in out.splitlines()[1]
    body = call(read_blocks, cfg_for(d["id"]), block_ids=[one["id"]])
    assert "ref [@rivera2024]: Rivera, A. and Chen, B. (2024). Fleet costs. Example Press. https://example.org/fleet" in body


def test_the_printed_entry_has_the_fields_and_never_the_note():
    assert refs.format_ref(RIVERA) == ("Rivera, A. and Chen, B. (2024). Fleet costs. Example Press. "
                                       "https://example.org/fleet Accessed 2026-01-05.")
    assert refs.format_ref({"title": "Report", "year": "2022", "note": "private"}) == "Report (2022)."
    assert refs.format_ref({"title": "https://example.org/x", "url": "https://example.org/x"}) == "https://example.org/x"
    assert "supports" not in refs.format_ref(RIVERA) and "private" not in refs.format_ref({"title": "T", "note": "private"})


def test_the_agent_can_set_the_fields_and_cite_by_key(store, cfg_for):
    d = store.create_document("A", {"approval": "auto"})
    b = store.insert_block(d["id"], "text", {"markdown": "x"})
    out = call(propose_edit, cfg_for(d["id"]), block_id=b["id"], content="Costs fell [@rivera2024].", references=[RIVERA])
    assert not out.startswith("ERROR"), out
    got = store.get_block(b["id"])
    assert got["refs"][0]["authors"] == "Rivera, A. and Chen, B." and got["refs"][0]["key"] == "rivera2024"
    assert "[@rivera2024]" in got["content"]["markdown"]


def test_import_reads_author_year_footnotes_into_fields_and_cites_by_key():
    imp = from_markdown("Costs fell.[^1] And again.[^1]\n\n[^1]: Rivera, A. (2024). Fleet costs. Example Press. https://example.org/fleet\n")
    assert imp.blocks[0]["content"]["markdown"] == "Costs fell.[@rivera2024] And again.[@rivera2024]"
    assert imp.blocks[0]["refs"] == [{"authors": "Rivera, A", "year": "2024", "title": "Fleet costs",
                                      "publisher": "Example Press", "url": "https://example.org/fleet", "key": "rivera2024"}]
    assert refs.parse_citation("Just a title, 2024 edition") == {"title": "Just a title, 2024 edition"}
    assert refs.parse_citation("(2024). No author here.") == {"title": "(2024). No author here."}
    # two different sources never share a key
    two = from_markdown("A.[^a] B.[^b]\n\n[^a]: Acme Handbook\n[^b]: Acme Handbook, second edition\n")
    keys = [r["key"] for b in two.blocks for r in b.get("refs", [])]
    assert len(keys) == len(set(keys)) == 2


# ------------------------------------------------------------------------------ Word export
def _docx(store, doc_id):
    import docx

    from langstage_codraft.server import _references

    return docx.Document(io.BytesIO(exporters.to_docx(store, doc_id, {}, _references(store, doc_id))))


def test_word_export_prints_formatted_references_numbers_and_markdown_cells(store):
    d = store.create_document("A")
    store.insert_block(d["id"], "text", {"markdown": "Costs fell [@rivera2024]."}, refs=[RIVERA])
    store.insert_block(d["id"], "table", {"rows": [["Line", "Cost"], ["Vans", "$24.4m [@rivera2024]"], ["Fuel", "$3.4m"],
                                                    ["**Total**", "*$27.8m*"]], "total": True})
    store.insert_block(d["id"], "references", {})
    x = _docx(store, d["id"])
    text = "\n".join(p.text for p in x.paragraphs)
    assert "Costs fell [1]." in text and "[@" not in text
    assert "Rivera, A. and Chen, B. (2024). Fleet costs. Example Press. https://example.org/fleet" in text
    assert "supports the cost table" not in text                      # the private note is not printed
    t = x.tables[0]
    assert t.cell(1, 1).text == "$24.4m [1]"
    assert t.cell(3, 0).text == "Total" and all(r.bold for r in t.cell(3, 0).paragraphs[0].runs if r.text)
    assert all(r.bold for r in t.cell(3, 1).paragraphs[0].runs if r.text)   # the total row
    assert [r for r in t.cell(3, 1).paragraphs[0].runs if r.text][0].italic
    assert not any(r.bold for r in t.cell(2, 0).paragraphs[0].runs)
    assert "tblHeader" in t.rows[0]._tr.xml and "tblHeader" not in t.rows[1]._tr.xml   # repeats on each page


# ------------------------------------------------------------------------------ tables
def test_a_table_takes_a_total_flag_and_refuses_a_wrong_type(store):
    d = store.create_document("A")
    b = store.insert_block(d["id"], "table", {"rows": [["a", "b"], ["1", "2"]], "total": True})
    assert b["content"]["total"] is True
    with pytest.raises(Invalid, match="content.total of a table block must be true or false"):
        store.insert_block(d["id"], "table", {"rows": [["a"]], "total": "yes"})


# ------------------------------------------------------------------------------ bottom anchor
def test_anchor_bottom_is_a_layout_field_with_clear_refusals(store, cfg_for):
    d = store.create_document("A")
    b = store.insert_block(d["id"], "callout", {"markdown": "**Start a pilot.**"}, layout={"span": "full", "anchor": "bottom"})
    assert b["layout"]["anchor"] == "bottom"
    assert store.set_layout(b["id"], {"anchor": "none"})["layout"]["anchor"] is None
    with pytest.raises(Invalid, match="layout.anchor must be 'bottom' or null"):
        store.set_layout(b["id"], {"anchor": "top"})
    with pytest.raises(Invalid, match="can't also be anchored"):
        store.set_layout(b["id"], {"anchor": "bottom", "pin": {"page": 1, "top": 0}})
    # a block saved before 0.9.0 has no anchor
    assert store.insert_block(d["id"], "text", {"markdown": "x"})["layout"]["anchor"] is None


def test_the_agent_anchors_instead_of_pinning_and_each_replaces_the_other(store, cfg_for):
    d = store.create_document("A")
    cfg = cfg_for(d["id"])
    b = store.insert_block(d["id"], "callout", {"markdown": "CTA"}, layout={"pin": {"anchor": "page", "page": 1, "x": 68, "y": 812, "w": 680, "h": 176}})
    out = call(set_block_layout, cfg, block_id=b["id"], anchor="bottom")
    lay = store.get_block(b["id"])["layout"]
    assert not out.startswith("ERROR") and lay["anchor"] == "bottom" and lay["pin"] is None
    call(set_block_layout, cfg, block_id=b["id"], pin_page=1, pin_top_px=40)          # pinning drops the anchor
    lay = store.get_block(b["id"])["layout"]
    assert lay["anchor"] is None and lay["pin"]["top"] == 40
    call(set_block_layout, cfg, block_id=b["id"], anchor="bottom")
    assert call(set_block_layout, cfg, block_id=b["id"], anchor="none").count('"anchor": null') == 1
    assert 'anchor="bottom"' in set_block_layout.description


# ------------------------------------------------------------------------------ reserved areas
def test_reserving_again_replaces_the_area_it_overlaps(store, cfg_for):
    d = store.create_document("A")
    cfg = cfg_for(d["id"])
    spacers = lambda: [b for b in store.list_blocks(d["id"]) if b["type"] == "spacer"]  # noqa: E731
    call(reserve_area, cfg, page=1, x_px=68, y_px=924, w_px=680, h_px=64)
    call(reserve_area, cfg, page=1, x_px=68, y_px=856, w_px=680, h_px=132)
    out = call(reserve_area, cfg, page=1, x_px=68, y_px=812, w_px=680, h_px=176)
    assert "replaced" in out and len(spacers()) == 1 and spacers()[0]["layout"]["pin"]["h"] == 176
    # one that doesn't overlap, or sits on another page, is kept
    call(reserve_area, cfg, page=1, x_px=68, y_px=100, w_px=200, h_px=100)
    call(reserve_area, cfg, page=2, x_px=68, y_px=812, w_px=680, h_px=176)
    assert len(spacers()) == 3
    # a repeated area covers every page, so it replaces the ones under it
    call(reserve_area, cfg, page=1, x_px=68, y_px=800, w_px=680, h_px=200, repeat="all")
    assert sorted(s["layout"]["pin"]["y"] for s in spacers()) == [100, 800]
    out = call(reserve_area, cfg, page=1, x_px=0, y_px=0, w_px=900, h_px=1200, remove=True)
    assert out.startswith("Removed 2 reserved area(s)") and spacers() == []
    assert call(reserve_area, cfg, page=1, x_px=0, y_px=0, w_px=10, h_px=10, remove=True) == "No reserved area overlaps that rectangle."
    # removed areas can be restored like any deleted block
    assert len(store.deleted_blocks(d["id"])) >= 2
    # other pinned blocks are never touched
    art = store.insert_block(d["id"], "image", {}, layout={"pin": {"anchor": "page", "page": 1, "x": 0, "y": 0, "w": 300, "h": 300}})
    call(reserve_area, cfg, page=1, x_px=0, y_px=0, w_px=300, h_px=300)
    assert store.get_block(art["id"])["type"] == "image"


# ------------------------------------------------------------------------------ back-fill
def test_backfill_is_on_for_new_documents_and_off_for_ones_saved_before_it(store, cfg_for):
    d = store.create_document("New")
    assert d["settings"]["backfill"] is True
    assert "back-fill on" in call(get_document, cfg_for(d["id"]))
    # a document saved by an older version has no such setting: its pages stay as they were
    old = json.loads(store._one("SELECT settings FROM documents WHERE id = ?", (d["id"],))["settings"])
    del old["backfill"]
    store._db.execute("UPDATE documents SET settings = ? WHERE id = ?", (json.dumps(old), d["id"]))
    store._db.commit()
    assert store.get_document(d["id"])["settings"]["backfill"] is False
    assert "back-fill off" in call(get_document, cfg_for(d["id"]))
    # an unrelated change doesn't switch it on behind the author's back
    store.update_document(d["id"], settings={"columns": 2})
    assert store.get_document(d["id"])["settings"]["backfill"] is False
    # the author or the agent turns it on, and it stays on
    assert not call(set_document_style, cfg_for(d["id"]), backfill=True).startswith("ERROR")
    assert store.get_document(d["id"])["settings"]["backfill"] is True
    with pytest.raises(Invalid, match="settings.backfill must be true or false"):
        store.update_document(d["id"], settings={"backfill": "yes"})
    # a copy keeps the original's choice
    store.update_document(d["id"], settings={"backfill": False})
    assert store.duplicate_document(d["id"])["settings"]["backfill"] is False


# ------------------------------------------------------------------------------ overflow
class _Shots:
    def call(self, value):
        return value

    def screenshot_pages(self, doc_id, pages=None, max_pages=4, preview=False):
        return [{"page": 1, "path": "p1.jpg", "total": 2, "more": [], "blocks": ["blk_a", "blk_b"], "overflow": ["blk_b"]},
                {"page": 2, "path": "p2.jpg", "total": 2, "more": [], "blocks": ["blk_c"], "overflow": []}]


def test_view_pages_names_the_blocks_that_are_cut_off(store):
    d = store.create_document("A")
    text, shots = page_shots(_Shots(), d["id"])
    assert "OVERFLOW: blk_b on page 1" in text and "cut off" in text and len(shots) == 2


def test_view_pages_says_nothing_about_overflow_when_there_is_none(store):
    class Clean(_Shots):
        def screenshot_pages(self, *a, **k):
            return [{"page": 1, "path": "p1.jpg", "total": 1, "more": [], "blocks": []}]   # an older renderer: no key

    assert "OVERFLOW" not in page_shots(Clean(), store.create_document("A")["id"])[0]


# ------------------------------------------------------------------------------ charts
def test_chart_warnings_flag_mixed_units_and_look_alike_colours():
    mixed = {"data": {"values": [{"m": "Share", "v": 10, "label": "10%"}, {"m": "Count", "v": 30, "label": ">30 countries"},
                                 {"m": "Cover", "v": 70, "label": ">70%"}]},
             "layer": [{"mark": "bar", "encoding": {"y": {"field": "m"}, "x": {"field": "v", "type": "quantitative"}}},
                       {"mark": "text", "encoding": {"y": {"field": "m"}, "x": {"field": "v", "type": "quantitative"},
                                                     "text": {"field": "label"}}}]}
    w = chart_warnings(mixed)
    assert len(w) == 1 and "different units on one axis (%, countries)" in w[0]
    teal = {"mark": "bar", "encoding": {"color": {"field": "c", "scale": {"range": ["#0b2545", "#16697a", "#4fb3bf", "#7a8899",
                                                                                     "#0f766e", "#9dc3d4"]}}}}
    w = chart_warnings(teal)
    assert len(w) == 1 and "#16697a and #0f766e" in w[0]


def test_chart_warnings_are_quiet_for_a_sound_or_unreadable_spec():
    ok = {"data": {"values": [{"m": "A", "v": 10, "label": "10%"}, {"m": "B", "v": 30, "label": "30%"}]},
          "mark": "text", "encoding": {"text": {"field": "label"},
                                       "color": {"field": "m", "scale": {"range": ["#0b2545", "#ff5a1f", "#2f9e44"]}}}}
    assert chart_warnings(ok) == []
    for odd in (None, [], "spec", {"data": "url"}, {"layer": [1, None]}, {"encoding": {"color": {"scale": {"range": "category"}}}}):
        assert chart_warnings(odd) == []
