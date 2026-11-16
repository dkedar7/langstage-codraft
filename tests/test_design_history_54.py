"""Design history: the document's look can be stepped back like its content (gh #54).

The agent styles without approval, by design, so every change to the look is logged with
the state before it: document settings, typography, cover, header and footer, block
layouts and reserved areas. One agent turn is one step. A step can be undone alone, or the
look put back to how it was before it; the restore is a step too. Content is never touched.

The editor and a real-model round trip are in tests/e2e/test_design_history_54.py.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from langstage_codraft import design_history as dh
from langstage_codraft import store as store_mod
from langstage_codraft import tools as tools_mod
from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.design_history import changed_paths, design_of, summarize
from langstage_codraft.mcp_server import build_mcp_server
from langstage_codraft.middleware import CODRAFT_PROMPT
from langstage_codraft.server import create_app
from langstage_codraft.store import NotFound, Store
from langstage_codraft.tools import (
    CODRAFT_TOOLS,
    begin_turn,
    changes_note,
    list_design_history,
    propose_insert,
    reserve_area,
    restore_design,
    set_block_layout,
    set_cover,
    set_document_style,
    set_header_footer,
    set_typography,
)


@pytest.fixture
def clock(monkeypatch):
    """The store's clock, by hand: steps fold by time, so tests move it themselves."""
    now = [1_000_000.0]
    monkeypatch.setattr(store_mod.time, "time", lambda: now[0])

    def tick(seconds: float = 60.0) -> float:
        now[0] += seconds
        return now[0]

    return tick


def _doc(store, n=3):
    d = store.create_document("Look")
    blocks = [store.insert_block(d["id"], "text", {"markdown": f"Paragraph {i}."}, author="author") for i in range(n)]
    return d, blocks


def _look(store, doc_id):
    return design_of(store.get_document(doc_id)["settings"])


def _layouts(store, doc_id):
    return {b["id"]: b["layout"] for b in store.list_blocks(doc_id)}


def _content(store, doc_id):
    return [(b["id"], b["type"], b["content"], b["n_versions"]) for b in store.list_blocks(doc_id)]


# ------------------------------------------------------------------ what is logged
def test_a_change_to_the_look_is_a_step_with_who_and_what(store, clock):
    d, _ = _doc(store)
    assert store.design_history(d["id"]) == []
    store.update_document(d["id"], settings={"columns": 2, "style": {"font_body": "'Lato', sans-serif"}}, by="Dana")
    [step] = store.design_history(d["id"])
    assert step["by"] == "Dana" and step["kind"] == "author" and step["restore_of"] is None
    assert step["summary"] == "1 → 2 columns, body font Lato"
    assert step["settings"] is True and step["blocks"] == 0 and step["undoable"] is True
    assert step["id"].startswith("dsn_")


def test_only_the_look_is_history_not_the_brief_the_approval_mode_or_the_title(store, clock):
    d, _ = _doc(store)
    store.update_document(d["id"], title="Renamed", settings={"approval": "auto", "min_pages": 3,
                                                               "brief": {"audience": "the board"}})
    assert store.design_history(d["id"]) == []
    assert set(dh.NON_DESIGN) == {"approval", "brief", "min_pages"}
    assert "approval" not in design_of(store.get_document(d["id"])["settings"])


def test_a_block_layout_change_is_a_step_and_a_no_op_is_not(store, clock):
    d, (a, b, _) = _doc(store)
    store.set_layout(a["id"], {"span": "column"})  # what it already is
    assert store.design_history(d["id"]) == []
    store.set_layout(a["id"], {"span": "full", "variant": "lead"})
    store.set_layout(b["id"], {"height": 200})
    [step] = store.design_history(d["id"])
    assert step["summary"] == "2 block layouts" and step["blocks"] == 2 and step["settings"] is False
    clock()
    store.set_layout(a["id"], {"dropcap": True}, log=False)  # a trial: the caller logs it, or not
    assert len(store.design_history(d["id"])) == 1
    store.log_design(d["id"], by="agent", layouts_before={a["id"]: {**a["layout"], "span": "full", "variant": "lead"}})
    assert [s["kind"] for s in store.design_history(d["id"])] == ["agent", "author"]


def test_an_authors_quick_changes_are_one_step_and_later_ones_another(store, clock):
    d, _ = _doc(store)
    for size in (14, 14.5, 15, 16):  # a slider
        store.update_document(d["id"], settings={"style": {"font_size": size}})
        clock(2)
    assert [s["summary"] for s in store.design_history(d["id"])] == ["body size 16"]
    clock(30)
    store.update_document(d["id"], settings={"style": {"color_accent": "#ff5a1f"}})
    assert [s["summary"] for s in store.design_history(d["id"])] == ["accent colour #ff5a1f", "body size 16"]


def test_one_agent_turn_is_one_step_however_many_calls_and_however_long(store, clock):
    d, (a, b, c) = _doc(store)
    for i, blk in enumerate((a, b, c)):
        store.update_document(d["id"], settings={"style": {"font_size": 13 + i}}, by="agent", group="turn:1")
        clock(200)  # a slow turn is still one turn
        store.set_layout(blk["id"], {"span": "full"}, by="agent", group="turn:1")
    [step] = store.design_history(d["id"])
    assert step["kind"] == "agent" and step["summary"] == "body size 15, 3 block layouts"
    store.update_document(d["id"], settings={"columns": 2}, by="agent", group="turn:2")
    assert [s["summary"] for s in store.design_history(d["id"])] == ["1 → 2 columns", "body size 15, 3 block layouts"]


def test_an_agent_with_no_turn_groups_by_time_and_an_author_change_ends_the_step(store, clock):
    d, (a, _, _) = _doc(store)
    store.update_document(d["id"], settings={"columns": 2}, by="agent")  # an MCP client: no turn
    clock(60)
    store.set_layout(a["id"], {"span": "full"}, by="agent")
    assert len(store.design_history(d["id"])) == 1
    clock(5)
    store.update_document(d["id"], settings={"columns": 3}, by="Dana")
    clock(5)
    store.update_document(d["id"], settings={"style": {"font_size": 12}}, by="agent")
    assert [s["kind"] for s in store.design_history(d["id"])] == ["agent", "author", "agent"]
    clock(500)
    store.update_document(d["id"], settings={"style": {"font_size": 13}}, by="agent")
    assert len(store.design_history(d["id"])) == 4


def test_a_step_that_ends_where_it_started_is_not_listed(store, clock):
    d, (a, _, _) = _doc(store)
    store.update_document(d["id"], settings={"columns": 2})
    store.update_document(d["id"], settings={"columns": 1})
    store.set_layout(a["id"], {"span": "full"})
    store.set_layout(a["id"], {"span": "column"})
    assert store.design_history(d["id"]) == []


def test_history_survives_a_restart_and_is_capped(tmp_path, monkeypatch, clock):
    monkeypatch.setattr(store_mod, "DESIGN_CAP", 5)
    s = Store(tmp_path / "codraft.db")
    d = s.create_document("Look")
    for i in range(9):
        s.update_document(d["id"], settings={"style": {"font_size": 10 + i}})
        clock()
    s.close()
    s = Store(tmp_path / "codraft.db")
    steps = s.design_history(d["id"])
    assert [st["summary"] for st in steps] == [f"body size {17 - i} → {18 - i}" for i in range(5)]
    s.close()


# ------------------------------------------------------------------------ restoring
def _three_steps(store, clock):
    """An author change, an agent restyle (settings and layouts), then an author change."""
    d, (a, b, c) = _doc(store)
    store.update_document(d["id"], settings={"columns": 2}, by="Dana")
    clock()
    store.update_document(d["id"], settings={"style": {"font_body": "'Lato', sans-serif"}, "theme": "modern"},
                          by="agent", group="turn:1")
    store.set_layout(a["id"], {"span": "full", "variant": "lead"}, by="agent", group="turn:1")
    store.set_layout(b["id"], {"height": 240}, by="agent", group="turn:1")
    clock()
    store.update_document(d["id"], settings={"style": {"color_accent": "#ff5a1f"}}, by="Dana")
    clock()
    steps = store.design_history(d["id"])
    assert [s["kind"] for s in steps] == ["author", "agent", "author"]
    return d, (a, b, c), steps


def test_undoing_one_step_leaves_later_changes_in_place(store, clock):
    d, (a, b, _), (last, agent, first) = _three_steps(store, clock)
    content = _content(store, d["id"])
    got = store.restore_design(d["id"], agent["id"], only=True, by="Dana")
    assert got["changed"] and got["settings"] and got["blocks"] == 2 and got["kept"] == 0 and got["skipped"] == 0
    look = _look(store, d["id"])
    assert look["theme"] == "classic" and "font_body" not in look["style"]
    assert look["style"]["color_accent"] == "#ff5a1f" and look["columns"] == 2  # the author's own, before and after
    lays = _layouts(store, d["id"])
    assert lays[a["id"]]["span"] == "column" and lays[a["id"]]["variant"] is None and lays[b["id"]]["height"] is None
    assert _content(store, d["id"]) == content  # never the text, never a version
    steps = store.design_history(d["id"])
    assert steps[0]["restore_of"] == agent["id"] and steps[0]["note"] == "undo" and steps[0]["by"] == "Dana"
    assert steps[0]["restore_of_kind"] == "agent"
    assert next(s for s in steps if s["id"] == agent["id"])["undoable"] is False
    # again: nothing left to undo
    again = store.restore_design(d["id"], agent["id"], only=True)
    assert again["changed"] is False and again["step"] is None and len(store.design_history(d["id"])) == 4


def test_the_look_before_a_step_undoes_it_and_everything_after(store, clock):
    d, (a, b, _), (last, agent, first) = _three_steps(store, clock)
    before_restore = (_look(store, d["id"]), _layouts(store, d["id"]))
    content = _content(store, d["id"])
    got = store.restore_design(d["id"], agent["id"])
    assert got["changed"] and got["settings"] and got["blocks"] == 2
    look = _look(store, d["id"])
    assert look["columns"] == 2 and look["theme"] == "classic" and look["style"] == {}
    assert all(lay["span"] == "column" and lay["height"] is None for lay in _layouts(store, d["id"]).values())
    assert _content(store, d["id"]) == content
    # the restore is a step: undoing it brings back the agent's restyle and the author's colour
    top = store.design_history(d["id"])[0]
    assert top["restore_of"] == agent["id"] and top["note"] == "restore"
    assert "theme modern → classic" in top["summary"] and "2 block layouts" in top["summary"]
    store.restore_design(d["id"], top["id"], only=True)
    assert (_look(store, d["id"]), _layouts(store, d["id"])) == before_restore
    # all the way back: before the very first step
    store.restore_design(d["id"], first["id"])
    assert _look(store, d["id"])["columns"] == 1


def test_what_was_restyled_again_since_is_left_alone(store, clock):
    d, (a, b, _) = _doc(store)
    store.update_document(d["id"], settings={"columns": 2, "style": {"font_body": "'Lato', sans-serif"}},
                          by="agent", group="turn:1")
    store.set_layout(a["id"], {"span": "full"}, by="agent", group="turn:1")
    store.set_layout(b["id"], {"variant": "lead"}, by="agent", group="turn:1")
    clock()
    store.update_document(d["id"], settings={"style": {"font_body": "'Inter', sans-serif"}}, by="Dana")
    store.set_layout(a["id"], {"span": "full", "dropcap": True}, by="Dana")
    agent = store.design_history(d["id"])[-1]
    got = store.restore_design(d["id"], agent["id"], only=True, by="Dana")
    assert got["kept"] == 2 and got["blocks"] == 1 and got["settings"]
    assert "restyled again since" in got["notes"][0]
    look, lays = _look(store, d["id"]), _layouts(store, d["id"])
    assert look["columns"] == 1 and look["style"]["font_body"] == "'Inter', sans-serif"  # Dana's font stands
    assert lays[a["id"]]["span"] == "full" and lays[a["id"]]["dropcap"] is True          # so does Dana's block
    assert lays[b["id"]]["variant"] is None


def test_deleted_blocks_are_skipped_and_new_ones_untouched(store, clock):
    d, (a, b, c) = _doc(store)
    store.set_layout(a["id"], {"span": "full"}, by="agent", group="turn:1")
    store.set_layout(b["id"], {"span": "full"}, by="agent", group="turn:1")
    clock()
    store.delete_block(a["id"])
    new = store.insert_block(d["id"], "text", {"markdown": "Added later."}, layout={"span": "full"}, author="author")
    step = store.design_history(d["id"])[0]
    assert step["blocks"] == 1  # the deleted one no longer counts
    got = store.restore_design(d["id"], step["id"])
    assert got["blocks"] == 1 and got["skipped"] == 1 and "skipped" in got["notes"][0]
    lays = _layouts(store, d["id"])
    assert a["id"] not in lays and lays[b["id"]]["span"] == "column" and lays[new["id"]]["span"] == "full"
    assert [x["id"] for x in store.deleted_blocks(d["id"])] == [a["id"]]  # never recreated


def test_reserved_areas_come_and_go_with_their_step(store, clock):
    d, _ = _doc(store)
    one = store.reserve_area(d["id"], {"page": 1, "x": 40, "y": 40, "w": 300, "h": 200}, group="turn:1")["block"]
    clock()
    two = store.reserve_area(d["id"], {"page": 1, "x": 60, "y": 60, "w": 300, "h": 200}, group="turn:2")
    assert two["replaced"] == [one["id"]]
    steps = store.design_history(d["id"])
    assert [s["summary"] for s in steps] == ["1 reserved area added, 1 reserved area removed", "1 reserved area added"]
    got = store.restore_design(d["id"], steps[0]["id"], only=True)
    assert got["areas_removed"] == 1 and got["areas_restored"] == 1
    spacers = [b["id"] for b in store.list_blocks(d["id"]) if b["type"] == "spacer"]
    assert spacers == [one["id"]]  # the replaced one is back, the new one is gone
    # a move within one step leaves no trace of the first position
    d2, _ = _doc(store)
    first = store.reserve_area(d2["id"], {"page": 1, "x": 40, "y": 40, "w": 300, "h": 200}, group="turn:1")["block"]
    store.reserve_area(d2["id"], {"page": 1, "x": 60, "y": 60, "w": 300, "h": 200}, group="turn:1")
    [step] = store.design_history(d2["id"])
    assert step["summary"] == "1 reserved area added"
    store.restore_design(d2["id"], step["id"])
    assert [b for b in store.list_blocks(d2["id"]) if b["type"] == "spacer"] == []
    assert first["id"] in [x["id"] for x in store.deleted_blocks(d2["id"])]


def test_a_theme_that_no_longer_exists_keeps_the_current_one(store, clock, monkeypatch):
    from langstage_codraft import themes

    d, _ = _doc(store)
    store.update_document(d["id"], settings={"theme": "modern"})
    clock()
    store.update_document(d["id"], settings={"theme": "classic", "columns": 2})
    step = store.design_history(d["id"])[0]
    real = themes.theme_names()
    monkeypatch.setattr(themes, "theme_names", lambda: [n for n in real if n != "modern"])
    got = store.restore_design(d["id"], step["id"])
    assert got["changed"] and "no longer exists" in got["notes"][0]
    look = _look(store, d["id"])
    assert look["theme"] == "classic" and look["columns"] == 1


def test_a_step_of_another_document_is_refused(store, clock):
    d, _ = _doc(store)
    other, _ = _doc(store)
    store.update_document(other["id"], settings={"columns": 2})
    step = store.design_history(other["id"])[0]
    with pytest.raises(NotFound, match="no design step"):
        store.restore_design(d["id"], step["id"])
    assert _look(store, other["id"])["columns"] == 2


def test_last_agent_design_is_the_newest_one_still_in_effect(store, clock):
    d, _, (last, agent, first) = _three_steps(store, clock)
    assert store.last_agent_design(d["id"])["id"] == agent["id"]
    store.restore_design(d["id"], agent["id"], only=True)
    assert store.last_agent_design(d["id"]) is None


# -------------------------------------------------------------------- the summary
def test_summaries_say_what_changed_in_a_line():
    a = {"columns": 1, "theme": "classic", "style": {}, "margins": {"top": 20, "left": 20}, "backfill": False,
         "cover": {"align": None}}
    b = {"columns": 3, "theme": "modern", "style": {"font_body": "'Lato', sans-serif", "font_size": 13.0},
         "margins": {"top": 15, "left": 15}, "backfill": True, "cover": {"align": "center"}}
    assert changed_paths(a, b) == [("backfill",), ("columns",), ("cover", "align"), ("margins", "left"),
                                   ("margins", "top"), ("style", "font_body"), ("style", "font_size"), ("theme",)]
    assert summarize(a, b, layouts=14) == ("back-fill off → on, 1 → 3 columns, cover, margins, 3 more settings, "
                                           "14 block layouts")
    assert summarize(b, a, limit=9) == ("back-fill on → off, 3 → 1 columns, cover, margins, body font reset, "
                                        "body size reset, theme modern → classic")
    assert summarize({"style": {"font_body": "'Inter', sans-serif"}}, {"style": {"font_body": "'Lato', serif"}}) \
        == "body font Inter → Lato"
    assert summarize(None, None, layouts=1, areas_added=2) == "1 block layout, 2 reserved areas added"
    assert summarize(a, a) == "no visible change"
    assert dh.age(10) == "just now" and dh.age(200) == "3 min ago" and dh.age(7300) == "2 h ago"
    assert dh.age(90000) == "1 day ago"
    assert dh.who("agent", "agent", "Lighthouse") == "Lighthouse" and dh.who("user", "author") == "You"
    assert dh.who("Dana Rivera", "author") == "Dana Rivera"


# ------------------------------------------------------------------ the agent's tools
def test_a_turn_of_styling_tools_is_one_step_and_the_next_turn_another(store, cfg_for, clock):
    d, (a, b, _) = _doc(store)
    cfg = cfg_for(d["id"])
    begin_turn(d["id"], "msg-1")
    set_document_style.func(columns=2, theme="modern", config=cfg)
    clock(40)
    set_typography.func(font_body="Lato", font_size_px=13, config=cfg)
    set_cover.func(background="#002f58", config=cfg)
    set_header_footer.func(footer_right="{page}", config=cfg)
    clock(300)
    set_block_layout.func(block_id=a["id"], full_width=True, variant="lead", config=cfg)
    set_block_layout.func(block_id=b["id"], variant="small", config=cfg)
    reserve_area.func(page=1, x_px=40, y_px=40, w_px=200, h_px=120, config=cfg)
    [step] = store.design_history(d["id"])
    assert step["kind"] == "agent" and step["blocks"] == 2 and step["areas_added"] == 1
    assert "1 → 2 columns" in step["summary"] and "2 block layouts" in step["summary"]
    begin_turn(d["id"], "msg-2")
    set_typography.func(color_accent="#ff5a1f", config=cfg)
    assert [s["summary"] for s in store.design_history(d["id"])][0] == "accent colour #ff5a1f"
    assert len(store.design_history(d["id"])) == 2


def test_the_agent_lists_the_history_and_undoes_a_step_when_asked(store, cfg_for, clock):
    d, (a, _, _) = _doc(store)
    cfg = cfg_for(d["id"])
    assert "has not been changed" in list_design_history.func(config=cfg)
    begin_turn(d["id"], "msg-1")
    set_typography.func(font_body="Lato", config=cfg)
    set_block_layout.func(block_id=a["id"], full_width=True, config=cfg)
    clock()
    store.update_document(d["id"], settings={"columns": 2}, by="Dana")
    clock(120)
    text = list_design_history.func(config=cfg)
    lines = text.splitlines()
    step = store.design_history(d["id"])[1]
    assert lines[1].startswith("dsn_") and "the author (Dana): 1 → 2 columns" in lines[1] and "2 min ago" in lines[1]
    assert lines[2].startswith(step["id"]) and "you (the agent): body font Lato, 1 block layout" in lines[2]
    content = _content(store, d["id"])
    out = restore_design.func(step_id=step["id"], config=cfg)
    assert out.startswith("That step is undone: document settings, 1 block layout(s) put back. No content was changed.")
    assert "Design -> History" in out
    look = _look(store, d["id"])
    assert "font_body" not in look["style"] and look["columns"] == 2 and _content(store, d["id"]) == content
    top = store.design_history(d["id"])[0]
    assert top["kind"] == "agent" and top["restore_of"] == step["id"]
    assert "no longer in effect" in list_design_history.func(config=cfg)
    assert restore_design.func(step_id=step["id"], config=cfg).startswith(f"Nothing to undo for {step['id']}")
    assert restore_design.func(step_id="dsn_nope", config=cfg).startswith("ERROR: no design step dsn_nope")
    # whole_look: back to before the author's columns too
    first = store.design_history(d["id"])[-1]
    out = restore_design.func(step_id=first["id"], whole_look=True, config=cfg)
    assert out.startswith("The look is as it was before that step") and _look(store, d["id"])["columns"] == 1


def test_a_refused_layout_is_not_a_step(store, cfg_for, clock):
    d, (a, _, _) = _doc(store)
    store.edit_block(a["id"], content={"markdown": "word " * 400}, author="author")
    out = set_block_layout.func(block_id=a["id"], height_px=20, config=cfg_for(d["id"]))  # gh #58
    assert out.startswith("ERROR: REFUSED") and store.design_history(d["id"]) == []


def test_the_next_turn_is_told_the_author_undid_its_styling(store, cfg_for, clock):
    d, (a, _, _) = _doc(store)
    cfg = cfg_for(d["id"])
    begin_turn(d["id"], "msg-1")
    set_typography.func(font_body="Lato", config=cfg)
    since = clock()
    step = store.design_history(d["id"])[0]
    clock()
    store.restore_design(d["id"], step["id"], only=True, by="Dana")
    clock()
    store.update_document(d["id"], settings={"columns": 2}, by="Dana")
    note = changes_note(d["id"], since)
    assert "the author UNDID styling of yours (body font Lato → theme's)" in note or "UNDID styling of yours" in note
    assert "Do not apply it again unless asked." in note
    assert "the author changed the look themselves (1 → 2 columns)" in note
    assert changes_note(d["id"], clock()) == ""


def test_the_tools_are_in_the_set_over_mcp_and_in_the_prompt(store):
    names = [t.name for t in CODRAFT_TOOLS]
    assert "list_design_history" in names and "restore_design" in names and len(names) == 31
    assert "only" in restore_design.description.lower() and "never on your own initiative" in restore_design.description
    assert "list_design_history" in CODRAFT_PROMPT and "Never\n  restore a look on your own" in CODRAFT_PROMPT
    assert 'anchor="bottom"' in CODRAFT_PROMPT and "backfill=True" in CODRAFT_PROMPT


async def test_an_mcp_client_styles_lists_and_restores(store, clock):
    d, (a, _, _) = _doc(store)
    server = build_mcp_server()
    tools = {t.name for t in await server.list_tools()}
    assert {"list_design_history", "restore_design"} <= tools

    async def call(name, **args):
        res = await server.call_tool(name, {"doc_id": d["id"], **args})
        content = res[0] if isinstance(res, tuple) else getattr(res, "content", res)
        return "\n".join(getattr(c, "text", "") for c in content)

    tools_mod._TURNS.pop(d["id"], None)  # an MCP client has no turns: a burst is one step
    await call("set_document_style", columns=2)
    clock(20)
    await call("set_block_layout", block_id=a["id"], full_width=True)
    [step] = store.design_history(d["id"])
    assert step["kind"] == "agent" and step["summary"] == "1 → 2 columns, 1 block layout"
    assert step["id"] in await call("list_design_history")
    assert "That step is undone" in await call("restore_design", step_id=step["id"])
    assert _look(store, d["id"])["columns"] == 1 and _layouts(store, d["id"])[a["id"]]["span"] == "column"


def test_a_chart_with_mixed_units_or_look_alike_colours_comes_back_with_a_warning(store, cfg_for):
    d, _ = _doc(store)
    spec = {"data": {"values": [{"k": "Share", "v": 42, "label": "42%"}, {"k": "Countries", "v": 31, "label": "31 countries"}]},
            "layer": [{"mark": "bar", "encoding": {"x": {"field": "k"}, "y": {"field": "v", "type": "quantitative"}}},
                      {"mark": "text", "encoding": {"x": {"field": "k"}, "y": {"field": "v"}, "text": {"field": "label"}}}]}
    out = propose_insert.func(block_type="chart", content=json.dumps(spec), config=cfg_for(d["id"]))
    assert "waiting for the author's approval" in out
    assert "CHART CHECK: The chart puts different units on one axis (%, countries)" in out
    fine = {"data": {"values": [{"k": "A", "v": 1}]}, "mark": "bar",
            "encoding": {"x": {"field": "k"}, "y": {"field": "v", "type": "quantitative"}}}
    assert "CHART CHECK" not in propose_insert.func(block_type="chart", content=json.dumps(fine), config=cfg_for(d["id"]))


# ------------------------------------------------------------------------- REST
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    store = Store(tmp_path / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []}, store=store)
    with TestClient(app) as c:
        c.store = store
        yield c


def test_rest_lists_the_history_and_restores(client):
    did = client.post("/api/docs", json={"title": "Look"}).json()["id"]
    b = client.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Body."}}).json()
    assert client.get(f"/api/docs/{did}/design-history").json() == {"steps": []}
    client.patch(f"/api/docs/{did}", json={"settings": {"style": {"font_body": "'Lato', sans-serif",
                                                                  "color_heading": "#002f58"}, "columns": 2}})
    client.patch(f"/api/blocks/{b['id']}", json={"layout": {"span": "full", "variant": "lead"}})
    [step] = client.get(f"/api/docs/{did}/design-history").json()["steps"]
    assert step["by"] == "user" and step["kind"] == "author"
    assert step["summary"] == "1 → 2 columns, heading colour #002f58, body font Lato, 1 block layout"
    client.store.update_document(did, settings={"style": {"font_body": "'Inter', sans-serif"}, "columns": 3},
                                 by="agent", group="turn:9")
    steps = client.get(f"/api/docs/{did}/design-history", params={"limit": 1}).json()["steps"]
    assert len(steps) == 1 and steps[0]["kind"] == "agent" and "2 → 3 columns" in steps[0]["summary"]
    # undo the agent's step alone
    r = client.post(f"/api/docs/{did}/design-history/{steps[0]['id']}/restore", json={"only": True})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["changed"] and body["result"]["settings"] and body["result"]["step"].startswith("dsn_")
    assert body["doc"]["settings"]["columns"] == 2 and body["doc"]["settings"]["style"]["font_body"] == "'Lato', sans-serif"
    assert body["doc"]["blocks"][0]["layout"]["variant"] == "lead" and body["doc"]["blocks"][0]["n_versions"] == 1
    # the look before the first step: everything back
    r = client.post(f"/api/docs/{did}/design-history/{step['id']}/restore", json={})
    doc = r.json()["doc"]
    assert doc["settings"]["columns"] == 1 and doc["settings"]["style"] == {}
    assert doc["blocks"][0]["layout"]["span"] == "column" and doc["blocks"][0]["content"] == {"markdown": "Body."}
    assert client.post(f"/api/docs/{did}/design-history/dsn_nope/restore", json={}).status_code == 404
    assert client.get("/api/docs/doc_nope/design-history").status_code == 404
