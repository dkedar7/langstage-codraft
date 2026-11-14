"""Layout building blocks (gh #52): a row of cells, number tiles, and a fill behind a block.

A strong model asked for "three value props side by side, a row of three numbers, an orange
header band and a call-to-action box" pinned every block to pixel coordinates (266 tool calls,
190 of them set_block_layout) because nothing that flows could do it. These blocks can.
"""
import io
import json
import re

import pytest
from fastapi.testclient import TestClient

from langstage_codraft import importers, themes
from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.exporters import to_docx
from langstage_codraft.mcp_server import describe_tools
from langstage_codraft.middleware import CODRAFT_PROMPT
from langstage_codraft.server import create_app
from langstage_codraft.store import (
    BLOCK_TYPES,
    BLOCK_VARIANTS,
    VARIANT_NOTES,
    Invalid,
    plain_text,
    repair_content,
)
from langstage_codraft.templates import BUILTIN_TEMPLATES
from langstage_codraft.tools import (
    get_document,
    insert_blocks,
    parse_content,
    propose_edit,
    propose_insert,
    read_blocks,
    search_document,
    set_block_layout,
)

CELLS = [{"markdown": "**Fast.** Sub-second."}, {"markdown": "**Safe.** Signed."}, {"markdown": "**Fair.** Rate limits."}]
TILES = [{"value": "99.99%", "label": "delivered in 60 s"}, {"value": "2.1 billion", "label": "events a month"}]


def call(tool, cfg, **args):
    return tool.invoke(args, config=cfg)


# ------------------------------------------------------------------- the store
def test_row_and_stat_are_block_types_with_defaults(store):
    assert {"row", "stat"} <= set(BLOCK_TYPES)
    d = store.create_document("Doc")
    row = store.insert_block(d["id"], "row")
    assert row["content"] == {"cells": [{"markdown": ""}, {"markdown": ""}], "widths": None, "gap": None}
    stat = store.insert_block(d["id"], "stat")
    assert len(stat["content"]["items"]) == 3 and stat["content"]["items"][0] == {"value": "", "label": ""}
    assert row["layout"]["fill"] is None


def test_a_row_takes_two_to_four_markdown_cells(store):
    d = store.create_document("Doc")
    b = store.insert_block(d["id"], "row", {"cells": ["plain text", {"markdown": ["two", "paragraphs"]}, {"markdown": "c", "x": 1}],
                                             "widths": [1, 1, 2], "gap": 6})
    assert b["content"]["cells"] == [{"markdown": "plain text"}, {"markdown": "two\n\nparagraphs"}, {"markdown": "c"}]
    assert b["content"]["widths"] == [1, 1, 2] and b["content"]["gap"] == 6
    for bad, word in (
        ({"cells": [{"markdown": "only one"}]}, "2 to 4"),
        ({"cells": [{"markdown": "x"}] * 5}, "2 to 4"),
        ({"cells": "a|b"}, "2 to 4"),
        ({"cells": [{"markdown": 3}, {"markdown": "b"}]}, "cells[0]"),
        ({"cells": [None, "b"]}, "cells[0]"),
        ({"cells": ["a", "b"], "widths": [1, 2, 3]}, "3 entries for 2 cells"),
        ({"cells": ["a", "b"], "widths": [1, 0]}, "positive numbers"),
        ({"cells": ["a", "b"], "widths": "1,2"}, "positive numbers"),
        ({"cells": ["a", "b"], "gap": 80}, "0 to 40"),
        ({"cells": ["a", "b"], "gap": True}, "0 to 40"),
    ):
        with pytest.raises(Invalid, match=re.escape(word)):
            store.insert_block(d["id"], "row", bad)
    assert len(store.list_blocks(d["id"])) == 1  # a refused insert writes nothing (gh #28)


def test_number_tiles_take_values_and_labels(store):
    d = store.create_document("Doc")
    b = store.insert_block(d["id"], "stat", {"items": [{"value": 42, "label": "answers"}, ["38 ms", "latency"],
                                                         {"value": 2.5}, ["just a value"]]})
    assert b["content"]["items"] == [{"value": "42", "label": "answers"}, {"value": "38 ms", "label": "latency"},
                                     {"value": "2.5", "label": ""}, {"value": "just a value", "label": ""}]
    for bad, word in (({"items": []}, "1 to 6"), ({"items": [{"value": "x"}] * 7}, "1 to 6"), ({"items": "9"}, "1 to 6"),
                      ({"items": ["99%"]}, "items[0]"), ({"items": [{"value": ["a"]}]}, "value and label must be text"),
                      ({"items": [{"value": "a", "label": 3}]}, "value and label must be text")):
        with pytest.raises(Invalid, match=re.escape(word)):
            store.insert_block(d["id"], "stat", bad)


def test_editing_a_row_replaces_its_cells(store):
    d = store.create_document("Doc")
    b = store.insert_block(d["id"], "row", {"cells": CELLS})
    out = store.edit_block(b["id"], content={"cells": [{"markdown": "one"}, {"markdown": "two"}]})
    assert [c["markdown"] for c in out["content"]["cells"]] == ["one", "two"] and out["n_versions"] == 2
    with pytest.raises(Invalid):
        store.edit_block(b["id"], content={"cells": [{"markdown": "alone"}]})
    assert store.get_block(b["id"])["n_versions"] == 2


def test_a_damaged_row_or_stat_block_still_reads(store):
    assert repair_content("row", {"cells": "nope"})["cells"] == [{"markdown": ""}, {"markdown": ""}]
    assert repair_content("row", {"cells": [{"markdown": 5}, "b", None]})["cells"] == [
        {"markdown": "5"}, {"markdown": "b"}, {"markdown": ""}]
    assert repair_content("row", {"cells": ["a", "b"]})["cells"] == [{"markdown": "a"}, {"markdown": "b"}]
    assert repair_content("stat", {"items": None})["items"] == [{"value": "", "label": ""}]
    assert repair_content("stat", {"items": [{"value": 7, "label": None}, "junk"]})["items"] == [{"value": "7", "label": ""}]
    good = {"items": [{"value": "1", "label": "one"}]}
    assert repair_content("stat", good) == good


def test_plain_text_reads_cells_and_tiles(store):
    assert " ".join(plain_text("row", {"cells": CELLS}).split()) == "Fast. Sub second. Safe. Signed. Fair. Rate limits."
    assert plain_text("stat", {"items": TILES}) == "99.99% delivered in 60 s; 2.1 billion events a month"
    assert plain_text("row", {"cells": "bad"}) == "" and plain_text("stat", {}) == ""


# ------------------------------------------------------------- variants and fill
def test_band_cards_and_plain_are_variants_with_notes():
    assert "band" in BLOCK_VARIANTS["heading"] and "band" in BLOCK_VARIANTS["callout"]
    assert BLOCK_VARIANTS["row"] == ("cards",) and BLOCK_VARIANTS["stat"] == ("plain",)
    for v in ("band", "cards", "plain"):
        assert VARIANT_NOTES[v]


def test_fill_is_a_theme_colour_name_or_a_hex_colour(store):
    d = store.create_document("Doc")
    h = store.insert_block(d["id"], "heading", {"text": "Acme Relay"}, layout={"variant": "band", "span": "full"})
    assert h["layout"]["variant"] == "band" and h["layout"]["fill"] is None
    assert store.set_layout(h["id"], {"fill": "#FF5a1f"})["layout"]["fill"] == "#FF5a1f"
    assert store.set_layout(h["id"], {"fill": "Accent"})["layout"]["fill"] == "accent"
    assert store.set_layout(h["id"], {"fill": "none"})["layout"]["fill"] is None
    for bad in ("orange", "rgb(1,2,3)", "#12345", 7, "url(x)", "#ff5a1f; color: red"):
        with pytest.raises(Invalid, match="layout.fill"):
            store.set_layout(h["id"], {"fill": bad})
    t = store.insert_block(d["id"], "text", {"markdown": "Body."})
    with pytest.raises(Invalid, match="fill doesn't apply to a text block"):
        store.set_layout(t["id"], {"fill": "accent"})
    with pytest.raises(Invalid, match="doesn't apply to a text block"):
        store.set_layout(t["id"], {"variant": "band"})
    with pytest.raises(Invalid, match="fill doesn't apply"):
        store.insert_block(d["id"], "image", {}, layout={"fill": "#000000"})
    for ok in ("callout", "row", "stat"):
        assert store.insert_block(d["id"], ok, None, layout={"fill": "muted"})["layout"]["fill"] == "muted"


def test_resolve_fill_picks_the_colour_and_the_text_that_reads_on_it():
    theme = {"color_accent": "#ff5a1f", "color_text": "#111111", "color_callout": "#fff4e6", "color_muted": "#666"}
    assert themes.resolve_fill("accent", theme) == ("#ff5a1f", True)      # white text on the brand orange
    assert themes.resolve_fill("callout", theme) == ("#fff4e6", False)    # dark text on a tint
    assert themes.resolve_fill("#1d3557", theme) == ("#1d3557", True)
    assert themes.resolve_fill("#fff", theme) == ("#fff", False)
    assert themes.resolve_fill("heading", theme) == ("#111111", True)     # no heading colour: the text colour
    assert themes.resolve_fill("figure", theme) == ("#fff4e6", False)     # no figure tint: the callout tint
    assert themes.resolve_fill("rule", theme) is None and themes.resolve_fill(None, theme) is None
    assert themes.is_fill("accent") and themes.is_fill("#abc") and not themes.is_fill("red") and not themes.is_fill(None)


# ----------------------------------------------------------------- agent tools
def test_the_agent_inserts_a_row_and_tiles_full_width(store, cfg_for):
    d = store.create_document("Doc", settings={"approval": "auto", "columns": 2})
    cfg = cfg_for(d["id"])
    out = call(insert_blocks, cfg, blocks=[
        {"type": "heading", "content": "Acme Relay", "level": 1, "layout": {"variant": "band", "span": "full"}},
        {"type": "row", "content": {"cells": ["**Fast.** x", "**Safe.** y", "**Fair.** z"]}, "layout": {"variant": "cards"}},
        {"type": "stat", "content": [{"value": "99.99%", "label": "delivered"}, ["38 ms", "latency"]]},
        {"type": "row", "content": "left cell\n\n---\n\nright cell", "layout": {"span": "column"}},
        {"type": "stat", "content": "99.99% | delivered\n2.1 billion | events a month\n38 ms"},
        {"type": "callout", "content": "**Start a pilot.**", "layout": {"variant": "band", "fill": "#ff5a1f"}},
    ])
    assert "ERROR" not in out, out
    head, row, stat, row2, stat2, cta = store.list_blocks(d["id"])
    assert head["layout"]["variant"] == "band" and head["layout"]["span"] == "full"
    assert [c["markdown"] for c in row["content"]["cells"]] == ["**Fast.** x", "**Safe.** y", "**Fair.** z"]
    assert row["layout"]["span"] == "full" and row["layout"]["variant"] == "cards"   # wide unless told otherwise
    assert stat["content"]["items"] == [{"value": "99.99%", "label": "delivered"}, {"value": "38 ms", "label": "latency"}]
    assert stat["layout"]["span"] == "full"
    assert [c["markdown"] for c in row2["content"]["cells"]] == ["left cell", "right cell"] and row2["layout"]["span"] == "column"
    assert stat2["content"]["items"] == [{"value": "99.99%", "label": "delivered"},
                                         {"value": "2.1 billion", "label": "events a month"}, {"value": "38 ms", "label": ""}]
    assert cta["layout"]["variant"] == "band" and cta["layout"]["fill"] == "#ff5a1f"


def test_one_bad_item_does_not_lose_the_rest_of_a_batch(store, cfg_for):
    d = store.create_document("Doc", settings={"approval": "auto"})
    out = call(insert_blocks, cfg_for(d["id"]), blocks=[
        {"type": "text", "content": "Before."},
        {"type": "row", "content": "only one cell"},
        {"type": "stat", "content": ""},
        {"type": "row", "content": {"cells": ["a", "b", "c", "d", "e"]}},
        {"type": "text", "content": "After."},
    ]).splitlines()
    assert out[1].startswith("1: ERROR row content: a row needs 2 to 4 cells")
    assert out[2].startswith("2: ERROR stat content: number tiles need at least one item")
    assert out[3].startswith("3: ERROR") and "2 to 4" in out[3]
    assert [b["content"]["markdown"] for b in store.list_blocks(d["id"])] == ["Before.", "After."]


def test_propose_insert_and_edit_a_row(store, cfg_for):
    d = store.create_document("Doc", settings={"columns": 2})
    cfg = cfg_for(d["id"])
    out = call(propose_insert, cfg, block_type="row", content=json.dumps({"cells": ["a", "b"], "widths": [2, 1]}),
               variant="cards", fill="callout")
    sid = re.search(r"sug_\w+", out).group(0)
    p = store.get_suggestion(sid)["payload"]
    assert p["layout"] == {"span": "full", "variant": "cards", "fill": "callout"}
    assert p["content"]["cells"] == [{"markdown": "a"}, {"markdown": "b"}] and p["content"]["widths"] == [2, 1]
    store.accept_suggestion(sid)
    row = store.list_blocks(d["id"])[0]
    assert row["content"]["cells"] == [{"markdown": "a"}, {"markdown": "b"}] and row["content"]["widths"] == [2, 1]
    # the agent reads it back as the JSON it can send again
    seen = call(read_blocks, cfg, block_ids=[row["id"]])
    assert '"cells": [{"markdown": "a"}, {"markdown": "b"}]' in seen and '"widths": [2, 1]' in seen
    # a third cell: the two old widths no longer fit, so they are dropped, not left to mismatch
    out = call(propose_edit, cfg, block_id=row["id"], content='["a", "b", "c"]')
    store.accept_suggestion(re.search(r"sug_\w+", out).group(0))
    row = store.get_block(row["id"])
    assert len(row["content"]["cells"]) == 3 and row["content"]["widths"] is None
    assert call(propose_insert, cfg, block_type="row", content="one cell").startswith("ERROR")
    assert call(propose_insert, cfg, block_type="text", content="x", fill="accent").startswith("ERROR")


def test_set_block_layout_fills_and_clears(store, cfg_for):
    d = store.create_document("Doc")
    cfg = cfg_for(d["id"])
    h = store.insert_block(d["id"], "heading", {"text": "Acme"})
    out = call(set_block_layout, cfg, block_id=h["id"], variant="band", fill="#ff5a1f", full_width=True)
    lay = store.get_block(h["id"])["layout"]
    assert (lay["variant"], lay["fill"], lay["span"]) == ("band", "#ff5a1f", "full") and '"fill": "#ff5a1f"' in out
    call(set_block_layout, cfg, block_id=h["id"], fill="none")
    assert store.get_block(h["id"])["layout"]["fill"] is None and store.get_block(h["id"])["layout"]["variant"] == "band"
    assert call(set_block_layout, cfg, block_id=h["id"], fill="orange").startswith("ERROR")
    # a pending insert: the fill is part of the suggestion
    out = call(propose_insert, cfg, block_type="stat", content="1 | one\n2 | two")
    sid = re.search(r"sug_\w+", out).group(0)
    call(set_block_layout, cfg, block_id=sid, fill="accent", variant="plain")
    assert store.get_suggestion(sid)["payload"]["layout"]["fill"] == "accent"


def test_the_outline_and_search_see_the_new_blocks(store, cfg_for):
    d = store.create_document("Doc")
    cfg = cfg_for(d["id"])
    store.insert_block(d["id"], "heading", {"text": "Acme"}, layout={"variant": "band", "fill": "accent"})
    store.insert_block(d["id"], "row", {"cells": CELLS})
    store.insert_block(d["id"], "stat", {"items": TILES})
    out = call(get_document, cfg)
    assert "variant=band fill=accent" in out and "3 cells side by side" in out and "2 number tiles" in out
    assert "row: cards" in out and "stat: plain" in out and "band = a solid strip" in out
    hit = call(search_document, cfg, query="rate limits")
    assert "row" in hit and "Rate limits" in hit
    assert "2.1 billion" in call(search_document, cfg, query="billion events")


def test_the_agent_is_told_to_use_blocks_not_pins():
    flat = " ".join(CODRAFT_PROMPT.split())
    assert "`row` block" in flat and "`stat` block" in flat and "`band` variant" in flat
    assert "never with pins" in flat and "raw HTML" in flat
    for tool in (propose_insert, insert_blocks, set_block_layout):
        d = " ".join(tool.description.split())
        assert "row" in d and "stat" in d and "band" in d, tool.name
    assert "fill" in set_block_layout.description and "Do not pin blocks to build columns" in " ".join(
        set_block_layout.description.split())
    assert "row" in " ".join(propose_edit.description.split())
    assert "propose_insert" in describe_tools() and "set_block_layout" in describe_tools()   # the same tools over MCP


def test_parse_content_for_rows_and_tiles():
    assert parse_content("row", '{"cells": ["a", "b"], "widths": [1, 2]}') == {"cells": ["a", "b"], "widths": [1, 2]}
    assert parse_content("row", '["a", "b"]') == {"cells": ["a", "b"]}
    assert parse_content("row", ["a", "b"]) == {"cells": ["a", "b"]}
    assert parse_content("row", "## One\n\ntext\n---\n## Two\n\ntext\n\n----\n\nthree") == {
        "cells": ["## One\n\ntext", "## Two\n\ntext", "three"]}
    with pytest.raises(Invalid, match="2 to 4 cells"):
        parse_content("row", "just one")
    assert parse_content("stat", '{"items": [{"value": "1", "label": "one"}]}') == {"items": [{"value": "1", "label": "one"}]}
    assert parse_content("stat", '[["1", "one"], {"value": "2"}]') == {"items": [["1", "one"], {"value": "2"}]}
    assert parse_content("stat", "| 99% | uptime |\n|---|---|\n| 38 ms | latency |") == {
        "items": [{"value": "99%", "label": "uptime"}, {"value": "38 ms", "label": "latency"}]}
    with pytest.raises(Invalid, match="at least one item"):
        parse_content("stat", "  \n ")


# ------------------------------------------------------------ REST, templates, import
@pytest.fixture
def client(tmp_path, monkeypatch):
    from langstage_codraft.store import Store

    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8797}, use_toml=False)
    s = Store(tmp_path / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []}, store=s)
    with TestClient(app) as c:
        yield c


def test_the_rest_api_creates_edits_and_refuses_like_the_tools(client):
    did = client.post("/api/docs", json={}).json()["id"]
    r = client.post(f"/api/docs/{did}/blocks", json={"type": "row", "content": {"cells": ["a", "b", "c"]},
                                                      "layout": {"span": "full", "variant": "cards", "fill": "#1d3557"}})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["content"]["cells"][2] == {"markdown": "c"} and b["layout"]["fill"] == "#1d3557"
    assert client.patch(f"/api/blocks/{b['id']}", json={"content": {"cells": ["only"]}}).status_code == 400
    assert client.patch(f"/api/blocks/{b['id']}", json={"layout": {"fill": "nope"}}).status_code == 400
    ok = client.patch(f"/api/blocks/{b['id']}", json={"content": {"widths": [1, 1, 2]}, "layout": {"fill": None}})
    assert ok.status_code == 200 and ok.json()["content"]["widths"] == [1, 1, 2] and ok.json()["layout"]["fill"] is None
    s = client.post(f"/api/docs/{did}/blocks", json={"type": "stat", "content": {"items": TILES}})
    assert s.status_code == 200 and s.json()["content"]["items"] == TILES
    assert client.post(f"/api/docs/{did}/blocks", json={"type": "stat", "content": {"items": []}}).status_code == 400
    cfg = client.get("/api/config").json()
    assert set(cfg["block_variants"]["row"]) == {"cards"} and set(cfg["block_variants"]["stat"]) == {"plain"}
    assert "band" in cfg["block_variants"]["heading"] and "band" in cfg["block_variants"]["callout"]


def test_the_one_pager_template_uses_a_row_tiles_and_a_band(client):
    types = [b["type"] for b in BUILTIN_TEMPLATES["one-pager"]["blocks"]]
    assert "row" in types and "stat" in types
    blocks = client.post("/api/docs", json={"template": "one-pager", "title": "Acme Relay"}).json()["blocks"]
    assert [b["type"] for b in blocks] == ["heading", "text", "row", "stat", "callout"]
    assert blocks[-1]["layout"]["variant"] == "band" and len(blocks[2]["content"]["cells"]) == 3


def test_import_maps_nothing_to_the_layout_blocks():
    md = ("# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> [!NOTE]\n> A note.\n\n"
          "<div style='display:flex'><div>left</div><div>right</div></div>\n\n**99%** uptime\n")
    imp = importers.from_markdown(md)
    assert imp.blocks and not {b["type"] for b in imp.blocks} & {"row", "stat"}
    assert all(not (b.get("layout") or {}).get("fill") for b in imp.blocks)


# ------------------------------------------------------------------- Word export
def test_word_export_writes_a_row_and_tiles_as_borderless_tables_and_shades_a_band(store):
    import docx

    d = store.create_document("Acme Relay", settings={"style": {"color_accent": "#ff5a1f"}})
    did = d["id"]
    store.insert_block(did, "heading", {"text": "Acme Relay", "level": 1}, layout={"variant": "band"})
    store.insert_block(did, "row", {"cells": [{"markdown": "### Reliable\n\nRetries for **72 hours**."},
                                              {"markdown": "- signed\n- logged"}, {"markdown": "Fair."}],
                                    "widths": [2, 1, 1]}, layout={"variant": "cards"})
    store.insert_block(did, "stat", {"items": TILES}, layout={"fill": "#1d3557"})
    store.insert_block(did, "stat", {"items": TILES}, layout={"variant": "plain"})
    store.insert_block(did, "callout", {"markdown": "**Start a pilot.** Write to relay@example.org."}, layout={"variant": "band"})
    out = docx.Document(io.BytesIO(to_docx(store, did, {}, [])))
    row, tiles, plain = out.tables
    assert [c.text for c in row.rows[0].cells] == ["Reliable\nRetries for 72 hours.", "• signed\n• logged", "Fair."]
    assert row.style is None or "Grid" not in row.style.name          # borderless: cells side by side, not a grid
    w = [c.width for c in row.rows[0].cells]
    assert w[0] > w[1] * 1.8 and abs(w[1] - w[2]) < 1000               # the 2:1:1 widths
    assert [c.text for c in tiles.rows[0].cells] == ["99.99%\ndelivered in 60 s", "2.1 billion\nevents a month"]
    xml = out.element.xml
    fills = re.findall(r'<w:shd [^>]*w:fill="([0-9A-F]{6})"', xml)
    assert fills.count("FF5A1F") == 2            # the band heading and the band callout
    assert fills.count("1D3557") == 2            # the two filled tiles
    assert len(fills) == 2 + 2 + 3               # plus the three cards; the plain tiles have none
    head = next(p for p in out.paragraphs if p.text == "Acme Relay")
    assert str(head.runs[0].font.color.rgb) == "FFFFFF"               # white on the brand colour
    value = tiles.rows[0].cells[0].paragraphs[0]
    assert value.runs[0].bold and str(value.runs[0].font.color.rgb) == "FFFFFF"
    assert str(plain.rows[0].cells[0].paragraphs[0].runs[0].font.color.rgb) == "FF5A1F"   # the accent, no tile
