"""Rendering and editor fixes from the first evaluation with a strong model.

Deleting a block was final (the agent deleted sections to make a page count, and there was no
way back); heading variants ignored the author's heading settings without saying so; Word
export wrote a Markdown table as a paragraph of pipes; superscripts were printed as typed.
The browser side (captions that wrap, tables that keep words whole, hyphenation) is in
``tests/e2e/test_render_quality.py``."""
import base64
import io

import docx
import pytest
from fastapi.testclient import TestClient

from langstage_codraft import exporters, importers
from langstage_codraft.config import CodraftConfig
from langstage_codraft.server import create_app
from langstage_codraft.store import BLOCK_VARIANTS, VARIANT_NOTES, Invalid, NotFound, Store


def _app(tmp_path, **over):
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799, **over}, use_toml=False)
    return create_app(cfg, None, {"kind": "none"}, store=Store(tmp_path / "data" / "codraft.db"))


def _doc(store):
    d = store.create_document("D")
    a = store.insert_block(d["id"], "heading", {"text": "Findings", "level": 2})
    b = store.insert_block(d["id"], "text", {"markdown": "The **second** block."})
    c = store.insert_block(d["id"], "references", {"title": "References"})
    return d, a, b, c


# ------------------------------------------------------------------ restoring a deleted block
def test_a_deleted_block_comes_back_where_it_was_with_its_history(store):
    d, a, b, c = _doc(store)
    store.edit_block(b["id"], content={"markdown": "The second block, revised."})
    store.set_layout(b["id"], {"variant": "lead"})
    store.delete_block(b["id"])
    assert [x["id"] for x in store.list_blocks(d["id"])] == [a["id"], c["id"]]

    gone = store.deleted_blocks(d["id"])
    assert [g["id"] for g in gone] == [b["id"]]
    assert gone[0]["type"] == "text" and gone[0]["preview"] == "The second block, revised."
    assert gone[0]["deleted_at"] and gone[0]["n_versions"] == 2

    back = store.restore_block(b["id"])
    assert [x["id"] for x in store.list_blocks(d["id"])] == [a["id"], b["id"], c["id"]]  # same place
    assert back["content"]["markdown"] == "The second block, revised."
    assert back["layout"]["variant"] == "lead"
    assert len(store.versions(b["id"])["versions"]) == 2  # history intact: it can still step back
    assert store.deleted_blocks(d["id"]) == []


def test_the_deleted_list_is_newest_first_and_per_document(store):
    d, a, b, c = _doc(store)
    other = store.create_document("Other")
    x = store.insert_block(other["id"], "text", {"markdown": "Elsewhere."})
    for blk in (a, c, x):
        store.delete_block(blk["id"])
    assert [g["id"] for g in store.deleted_blocks(d["id"])] == [c["id"], a["id"]]
    assert [g["id"] for g in store.deleted_blocks(other["id"])] == [x["id"]]


def test_restore_refuses_what_it_cannot_restore(store):
    d, a, _, _ = _doc(store)
    with pytest.raises(Invalid, match="is not deleted"):
        store.restore_block(a["id"])
    with pytest.raises(NotFound):
        store.restore_block("blk_nope")
    store.delete_block(a["id"])
    store.delete_document(d["id"])
    with pytest.raises(NotFound):  # the document is gone: nothing to put the block back into
        store.restore_block(a["id"])


def test_restoring_bumps_the_revision_so_open_tabs_reload(store):
    d, a, _, _ = _doc(store)
    store.delete_block(a["id"])
    rev = store.rev(d["id"])
    store.restore_block(a["id"])
    assert store.rev(d["id"]) > rev


def test_a_block_restored_after_the_agents_delete_was_accepted(store):
    """The evaluation's case: the agent proposed deleting the references to save a page."""
    d, _, _, c = _doc(store)
    s = store.propose(d["id"], "delete", {}, block_id=c["id"], author="agent")
    store.accept_suggestion(s["id"])
    assert c["id"] not in [x["id"] for x in store.list_blocks(d["id"])]
    store.restore_block(c["id"])
    assert store.list_blocks(d["id"])[-1]["id"] == c["id"]


def test_a_store_from_before_deleted_at_still_opens_and_lists(tmp_path):
    """Blocks deleted before this release have no timestamp; they are listed all the same."""
    s = Store(tmp_path / "codraft.db")
    d, a, _, _ = _doc(s)
    s.delete_block(a["id"])
    s._db.execute("UPDATE blocks SET deleted_at = NULL")
    s._db.commit()
    s.close()
    s = Store(tmp_path / "codraft.db")
    assert [g["id"] for g in s.deleted_blocks(d["id"])] == [a["id"]]
    assert s.deleted_blocks(d["id"])[0]["deleted_at"] is None
    assert s.restore_block(a["id"])["id"] == a["id"]
    s.close()


def test_restore_over_rest_with_the_same_guards_as_other_writes(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        doc = c.post("/api/docs", json={"template": "blank"}).json()
        blk = c.post(f"/api/docs/{doc['id']}/blocks", json={"type": "text", "content": {"markdown": "Keep me."}}).json()
        assert c.delete(f"/api/blocks/{blk['id']}").status_code == 200
        listed = c.get(f"/api/docs/{doc['id']}/deleted-blocks").json()
        assert [g["id"] for g in listed] == [blk["id"]] and listed[0]["preview"] == "Keep me."

        evil = {"Origin": "https://evil.example", "content-type": "text/plain"}
        r = c.post(f"/api/blocks/{blk['id']}/restore", headers=evil, content=b"")
        assert r.status_code == 403 and "Cross-site request refused" in r.json()["detail"]
        assert c.get(f"/api/docs/{doc['id']}").json()["blocks"] == []  # still deleted

        r = c.post(f"/api/blocks/{blk['id']}/restore")
        assert r.status_code == 200 and r.json()["content"]["markdown"] == "Keep me."
        assert [b["id"] for b in c.get(f"/api/docs/{doc['id']}").json()["blocks"]] == [blk["id"]]
        assert c.get(f"/api/docs/{doc['id']}/deleted-blocks").json() == []

        assert c.post(f"/api/blocks/{blk['id']}/restore").status_code == 400     # not deleted
        assert c.post("/api/blocks/blk_nope/restore").status_code == 404
        assert c.get("/api/docs/doc_nope/deleted-blocks").status_code == 404


def test_restore_needs_the_password_when_one_is_set(tmp_path):
    with TestClient(_app(tmp_path, auth_password="pw"), client=("127.0.0.1", 5000)) as c:
        ok = {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}
        doc = c.post("/api/docs", json={"template": "blank"}, headers=ok).json()
        blk = c.post(f"/api/docs/{doc['id']}/blocks", json={"type": "text"}, headers=ok).json()
        c.delete(f"/api/blocks/{blk['id']}", headers=ok)
        assert c.post(f"/api/blocks/{blk['id']}/restore").status_code == 401
        assert c.get(f"/api/docs/{doc['id']}/deleted-blocks").status_code == 401
        assert c.post(f"/api/blocks/{blk['id']}/restore", headers=ok).status_code == 200


# ------------------------------------------------------------------------- heading variants
def test_every_variant_says_what_it_looks_like():
    names = {v for vs in BLOCK_VARIANTS.values() for v in vs}
    assert names == set(VARIANT_NOTES)
    assert all(len(n) > 10 for n in VARIANT_NOTES.values())
    # The two things an agent has to know before it reports a heading's look as changed.
    assert "ignores heading_case" in VARIANT_NOTES["display"]
    assert "replaces that default" in VARIANT_NOTES["section"]


def test_config_lists_the_variants_with_their_notes(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        v = c.get("/api/config").json()["block_variants"]
        assert list(v["heading"]) == list(BLOCK_VARIANTS["heading"])
        assert v["heading"]["rule"] == VARIANT_NOTES["rule"]
        assert set(v) == set(BLOCK_VARIANTS)


def test_a_saved_theme_remembers_which_heading_settings_the_author_chose(tmp_path):
    """Section and rule headings yield to the document's own heading settings. Saving the look
    as a theme moves those settings into the theme; they stay the author's choice."""
    with TestClient(_app(tmp_path)) as c:
        doc = c.post("/api/docs", json={"template": "blank"}).json()
        style = {"heading_case": "none", "heading_scale": 1.3}
        assert c.patch(f"/api/docs/{doc['id']}", json={"settings": {"style": style}}).status_code == 200
        r = c.post("/api/themes", json={"label": "Board pack", "doc_id": doc["id"]}).json()
        saved = r["themes"][r["name"]]
        assert saved["explicit"] == ["heading_case", "heading_scale"] and saved["heading_case"] == "none"

        # A second theme saved from a document that uses the first keeps the choices too.
        c.patch(f"/api/docs/{doc['id']}", json={"settings": {"theme": r["name"], "style": None}})
        c.patch(f"/api/docs/{doc['id']}", json={"settings": {"style": {"heading_weight": 500}}})
        r2 = c.post("/api/themes", json={"label": "Board pack 2", "doc_id": doc["id"]}).json()
        assert r2["themes"][r2["name"]]["explicit"] == ["heading_case", "heading_scale", "heading_weight"]

        # Explicit values saved without a document carry no choices.
        r3 = c.post("/api/themes", json={"label": "Plain", "values": {"font_size": 14}}).json()
        assert "explicit" not in r3["themes"][r3["name"]]


# ------------------------------------------------------------- Markdown in the Word and PPTX export
def test_inline_runs_know_superscript_and_subscript():
    runs = exporters.md_runs("W m^-2^ day^-1^, H~2~O, m<sup>2</sup>, x<sub>i</sub>")
    assert [(t, st) for t, st in runs if st] == [
        ("-2", {"sup": True}), ("-1", {"sup": True}), ("2", {"sub": True}), ("2", {"sup": True}), ("i", {"sub": True})]
    # A lone caret or tilde, or a pair with a space inside, is text; ~~x~~ is still strike.
    assert exporters.md_runs("about ~5 to ~10 and 2^10 or a ^ b ^ c") == [("about ~5 to ~10 and 2^10 or a ^ b ^ c", {})]
    assert ("old", {"strike": True}) in exporters.md_runs("~~old~~ new")
    assert exporters.plain("CO~2~ at 10^6^") == "CO 2  at 10 6"


TABLE_MD = """The costs:

| Cost line | Diesel | Electric |
|:----------|-------:|:--------:|
| Vehicles  | $15.41m | $24.40m |
| Grid \\| depot | $0.00m |

Then the text goes on."""


def test_a_markdown_table_is_parsed_with_its_alignment():
    blocks = exporters.md_blocks(TABLE_MD)
    assert [k for k, _ in blocks] == ["p", "table", "p"]
    rows, align = exporters.md_table(blocks[1][1])
    assert rows == [["Cost line", "Diesel", "Electric"], ["Vehicles", "$15.41m", "$24.40m"], ["Grid | depot", "$0.00m", ""]]
    assert align == ["left", "right", "center"]
    # Pipes in prose or in a code fence are not a table.
    assert [k for k, _ in exporters.md_blocks("a | b\nnot a table")] == ["p"]
    assert [k for k, _ in exporters.md_blocks("```\n| x | y |\n|---|---|\n```")] == ["code"]


def test_word_export_writes_real_tables_superscripts_and_alignment(store):
    d = store.create_document("Costs")
    store.insert_block(d["id"], "text", {"markdown": TABLE_MD + "\n\nFlux in W m^-2^ and CO~2~."})
    store.insert_block(d["id"], "table", {"rows": [["Path", "Total"], ["Diesel", "$27.68m"]],
                                          "header": True, "align": ["left", "right"]})
    x = docx.Document(io.BytesIO(exporters.to_docx(store, d["id"], {}, [])))

    assert len(x.tables) == 2
    md_table, block_table = x.tables
    assert [c.text for c in md_table.rows[0].cells] == ["Cost line", "Diesel", "Electric"]
    assert md_table.cell(2, 0).text == "Grid | depot"
    assert [r.bold for r in md_table.cell(0, 0).paragraphs[0].runs if r.text] == [True]   # header row
    assert [r.bold for r in md_table.cell(1, 0).paragraphs[0].runs if r.text] == [None]
    right, center = docx.enum.text.WD_ALIGN_PARAGRAPH.RIGHT, docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER
    assert md_table.cell(1, 1).paragraphs[0].alignment == right
    assert md_table.cell(1, 2).paragraphs[0].alignment == center
    assert block_table.cell(1, 1).paragraphs[0].alignment == right
    assert block_table.cell(1, 0).paragraphs[0].alignment == docx.enum.text.WD_ALIGN_PARAGRAPH.LEFT

    text = "\n".join(p.text for p in x.paragraphs)
    assert "|" not in text  # the table no longer leaks into a paragraph of pipes
    flux = next(p for p in x.paragraphs if p.text.startswith("Flux"))
    assert flux.text == "Flux in W m-2 and CO2."
    assert [r.text for r in flux.runs if r.font.superscript] == ["-2"]
    assert [r.text for r in flux.runs if r.font.subscript] == ["2"]


def test_importing_a_markdown_table_keeps_its_column_alignment():
    got = importers.from_markdown("| Item | Cost |\n|:--|--:|\n| Vans | $1.2m |\n")
    table = next(b for b in got.blocks if b["type"] == "table")
    assert table["content"]["align"] == ["left", "right"]
    plain = importers.from_markdown("| Item | Cost |\n|---|---|\n| Vans | $1.2m |\n")
    assert "align" not in next(b for b in plain.blocks if b["type"] == "table")["content"]
