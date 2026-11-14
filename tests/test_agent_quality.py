"""What a strong model tripped over when it wrote real documents through Codraft.

Each section is one finding from the 2026-11 real-model evaluation: batches that landed in
reverse order, an agent that couldn't see its own pending suggestions, duplicate and no-op
suggestions, ids it guessed, arithmetic done in its head, and so on.
"""
import itertools
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from langstage_codraft.middleware import CODRAFT_PROMPT, CodraftMiddleware
from langstage_codraft.store import Invalid, NoChange, NotFound
from langstage_codraft.tools import (
    calculate,
    changes_note,
    draw_figure,
    get_block_history,
    get_document,
    insert_blocks,
    page_shots,
    parse_content,
    propose_delete,
    propose_edit,
    propose_insert,
    propose_move,
    read_blocks,
    set_block_layout,
    view_pages,
)


def call(tool, cfg, **args):
    return tool.invoke(args, config=cfg)


def texts(store, doc_id):
    return [b["content"].get("markdown") or b["content"].get("text") for b in store.list_blocks(doc_id)]


def doc3(store):
    d = store.create_document("Doc")
    blocks = [store.insert_block(d["id"], "text", {"markdown": t}) for t in ("First.", "Second.", "Third.")]
    return d, blocks


BATCH = [{"type": "heading", "content": "H1", "level": 2}, {"type": "text", "content": "P1"},
         {"type": "heading", "content": "H2", "level": 2}, {"type": "text", "content": "P2"}]


# ----------------------------------------------------------- 1. a batch keeps its order
@pytest.mark.parametrize("anchor", ["end", "first", "start"])
def test_a_batch_of_inserts_lands_in_the_order_it_was_written(store, cfg_for, anchor):
    d, (a, b, c) = doc3(store)
    args = {"blocks": BATCH}
    if anchor == "first":
        args["after_block_id"] = a["id"]
    out = call(insert_blocks, cfg_for(d["id"]), **args)
    assert out.count("waiting for approval") == 4 and "They will land in this order" in out
    assert store.accept_all(d["id"])["count"] == 4
    want = {"end": ["First.", "Second.", "Third.", "H1", "P1", "H2", "P2"],
            "first": ["First.", "H1", "P1", "H2", "P2", "Second.", "Third."]}.get(anchor)
    if want:
        assert texts(store, d["id"]) == want


def test_rest_accept_all_keeps_a_batch_in_order(tmp_path, monkeypatch):
    from langstage_codraft.agent import build_demo_agent
    from langstage_codraft.config import CodraftConfig
    from langstage_codraft.server import create_app
    from langstage_codraft.store import Store

    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8798}, use_toml=False)
    store = Store(tmp_path / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []}, store=store)
    with TestClient(app) as client:
        did = client.post("/api/docs", json={"title": "Order"}).json()["id"]
        anchor = client.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Intro."}}).json()
        call(insert_blocks, {"configurable": {"thread_id": did}}, blocks=BATCH, after_block_id=anchor["id"])
        r = client.post(f"/api/docs/{did}/suggestions/accept-all").json()
        assert r["count"] == 4 and r["failed"] == [] and r["skipped"] == []
        got = [b["content"].get("markdown") or b["content"].get("text") for b in client.get(f"/api/docs/{did}").json()["blocks"]]
        assert got == ["Intro.", "H1", "P1", "H2", "P2"]


@pytest.mark.parametrize("anchor", ["end", "first"])
@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_a_batch_keeps_its_order_however_the_author_accepts_it(store, cfg_for, anchor, order):
    d, (a, b, c) = doc3(store)
    args = {"blocks": [{"type": "text", "content": t} for t in ("N1", "N2", "N3")]}
    if anchor == "first":
        args["after_block_id"] = a["id"]
    call(insert_blocks, cfg_for(d["id"]), **args)
    sugs = store.list_suggestions(d["id"])
    for i in order:
        store.accept_suggestion(sugs[i]["id"])
    want = (["First.", "Second.", "Third.", "N1", "N2", "N3"] if anchor == "end"
            else ["First.", "N1", "N2", "N3", "Second.", "Third."])
    assert texts(store, d["id"]) == want


def test_rejecting_the_middle_of_a_batch_keeps_the_rest_in_order(store, cfg_for):
    d, (a, b, c) = doc3(store)
    call(insert_blocks, cfg_for(d["id"]), blocks=[{"type": "text", "content": t} for t in ("N1", "N2", "N3")],
         after_block_id=a["id"])
    s1, s2, s3 = store.list_suggestions(d["id"])
    store.reject_suggestion(s2["id"])
    store.accept_suggestion(s3["id"])
    store.accept_suggestion(s1["id"])
    assert texts(store, d["id"]) == ["First.", "N1", "N3", "Second.", "Third."]


def test_separate_inserts_at_the_end_stay_in_the_order_proposed(store, cfg_for):
    d, _ = doc3(store)
    cfg = cfg_for(d["id"])
    call(propose_insert, cfg, block_type="heading", content="Appendix")
    call(propose_insert, cfg, block_type="text", content="Appendix body.")
    s1, s2 = store.list_suggestions(d["id"])
    store.accept_suggestion(s2["id"])  # the author takes the second one first
    store.accept_suggestion(s1["id"])
    assert texts(store, d["id"])[-2:] == ["Appendix", "Appendix body."]


def test_an_insert_can_follow_a_pending_insert_by_its_suggestion_id(store, cfg_for):
    d, (a, b, c) = doc3(store)
    cfg = cfg_for(d["id"])
    out = call(propose_insert, cfg, block_type="text", content="N1", after_block_id=a["id"])
    sid = store.list_suggestions(d["id"])[0]["id"]
    assert sid in out and "after_block_id" in out
    call(propose_insert, cfg, block_type="text", content="N2", after_block_id=sid)
    store.accept_all(d["id"])
    assert texts(store, d["id"]) == ["First.", "N1", "N2", "Second.", "Third."]
    # once accepted, the suggestion id resolves to the block it became
    call(propose_insert, cfg, block_type="text", content="N1b", after_block_id=sid)
    store.accept_all(d["id"])
    assert texts(store, d["id"]) == ["First.", "N1", "N1b", "N2", "Second.", "Third."]


def test_chained_moves_apply_in_dependency_order_not_creation_order(store, cfg_for):
    """The agent reordered a reversed batch with chained moves; parallel tool calls created
    them out of order and Accept all applied them as created."""
    d = store.create_document("Doc")
    ids = {t: store.insert_block(d["id"], "text", {"markdown": t})["id"] for t in ("D", "C", "B", "A")}
    cfg = cfg_for(d["id"])
    # want A B C D; the moves arrive last-first, as parallel tool calls can
    call(propose_move, cfg, block_id=ids["D"], after_block_id=ids["C"])
    call(propose_move, cfg, block_id=ids["C"], after_block_id=ids["B"])
    call(propose_move, cfg, block_id=ids["B"], after_block_id=ids["A"])
    call(propose_move, cfg, block_id=ids["A"], after_block_id=None)
    r = store.accept_all(d["id"])
    assert r["count"] == 4 and not r["failed"]
    assert texts(store, d["id"]) == ["A", "B", "C", "D"]


def test_accept_all_reports_every_suggestion_it_leaves_behind(store, cfg_for):
    d, (a, b, c) = doc3(store)
    cfg = cfg_for(d["id"])
    call(propose_delete, cfg, block_id=b["id"])
    call(propose_edit, cfg, block_id=b["id"], content="Second, revised.")
    call(propose_edit, cfg, block_id=c["id"], content="Third, revised.")
    r = store.accept_all(d["id"])
    assert r["count"] == 2
    (failed,) = r["failed"]
    assert failed["kind"] == "edit" and failed["block_id"] == b["id"] and "block deleted" in failed["reason"]
    assert r["skipped"] == [failed["id"]]
    assert store.list_suggestions(d["id"]) == []  # nothing is left pending without a word


# ------------------------------------------------- 2. the agent sees its pending changes
def _mixed(store, cfg_for):
    d, (a, b, c) = doc3(store)
    cfg = cfg_for(d["id"])
    call(insert_blocks, cfg, blocks=[{"type": "heading", "content": "## New section"},
                                     {"type": "text", "content": "New paragraph."}], after_block_id=a["id"])
    call(propose_edit, cfg, block_id=b["id"], content="Second, rewritten.", note="tighter")
    call(propose_delete, cfg, block_id=c["id"])
    call(propose_move, cfg, block_id=a["id"], after_block_id=b["id"])
    return d, (a, b, c), cfg


def test_the_preview_is_exactly_what_accept_all_produces(store, cfg_for):
    d, _, _ = _mixed(store, cfg_for)
    pv = store.preview(d["id"])
    assert pv["pending"] == 5 and pv["left_out"] == []
    want = [(e["block"]["type"], json.dumps(e["block"]["content"], sort_keys=True),
             e["block"]["layout"]["role"]) for e in pv["entries"] if not e["deleted"]]
    before = store.rev(d["id"])
    assert store.preview(d["id"])["pending"] == 5 and store.rev(d["id"]) == before  # read-only
    assert store.accept_all(d["id"])["count"] == 5
    got = [(b["type"], json.dumps(b["content"], sort_keys=True), b["layout"]["role"]) for b in store.list_blocks(d["id"])]
    assert got == want
    # the new section was anchored to "First.", so it follows that block to its new place
    assert texts(store, d["id"]) == ["Second, rewritten.", "First.", "New section", "New paragraph."]


def test_the_preview_keeps_cover_blocks_on_the_cover(store, cfg_for):
    d = store.create_document("Doc")
    title = store.insert_block(d["id"], "heading", {"text": "Title"}, layout={"role": "cover"})
    store.insert_block(d["id"], "text", {"markdown": "Body."})
    call(propose_insert, cfg_for(d["id"]), block_type="text", content="Subtitle", after_block_id=title["id"])
    pv = store.preview(d["id"])
    assert [(e["block"]["layout"]["role"]) for e in pv["entries"]] == ["cover", "cover", "flow"]
    store.accept_all(d["id"])
    assert [b["layout"]["role"] for b in store.list_blocks(d["id"])] == ["cover", "cover", "flow"]


def test_the_outline_shows_pending_suggestions_where_they_will_land(store, cfg_for):
    d, (a, b, c), cfg = _mixed(store, cfg_for)
    out = call(get_document, cfg)
    assert "5 suggestion(s) of yours are waiting" in out and "Do not propose them again" in out
    lines = [ln for ln in out.splitlines() if ln[:1].isdigit()]
    assert [ln.split("]")[0].split("[")[1][:4] for ln in lines] == ["blk_", "blk_", "sug_", "sug_", "blk_"]
    assert b["id"] in lines[0] and "PENDING EDIT" in lines[0] and "Second, rewritten." in lines[0]
    assert a["id"] in lines[1] and "PENDING MOVE" in lines[1]
    assert "PENDING INSERT" in lines[2] and "New section" in lines[2] and "h2" in lines[2]
    assert "PENDING INSERT" in lines[3] and "New paragraph." in lines[3]
    assert c["id"] in lines[4] and "PENDING DELETE" in lines[4]


def test_the_outline_is_unchanged_when_nothing_is_pending(store, cfg_for):
    d, (a, b, c) = doc3(store)
    out = call(get_document, cfg_for(d["id"]))
    assert "Blocks (reading order):" in out and "PENDING" not in out
    assert [ln.split("]")[0].split("[")[1] for ln in out.splitlines() if ln[:1].isdigit()] == [a["id"], b["id"], c["id"]]


def test_the_outline_says_which_pending_suggestions_will_not_apply(store, cfg_for):
    d, (a, b, c) = doc3(store)
    cfg = cfg_for(d["id"])
    call(propose_edit, cfg, block_id=b["id"], content="Agent's version.")
    store.edit_block(b["id"], content={"markdown": "The author's newer version."})
    out = call(get_document, cfg)
    assert "Pending but it will not apply" in out and "edited after this was proposed" in out
    assert "The author's newer version." in out and "PENDING EDIT" not in out


def test_read_blocks_shows_the_accepted_text_and_the_pending_edit(store, cfg_for):
    d, (a, b, c), cfg = _mixed(store, cfg_for)
    ins = [s["id"] for s in store.list_suggestions(d["id"]) if s["kind"] == "insert"]
    out = call(read_blocks, cfg, block_ids=[b["id"], ins[1], c["id"]])
    assert "ACCEPTED (what the author's document has now, 1 words):\nSecond." in out
    assert "YOUR PENDING EDIT sug_" in out and "Second, rewritten." in out
    assert f"[{ins[1]}] text · PENDING INSERT" in out and "New paragraph." in out
    assert "PENDING DELETE" in out


def test_view_pages_renders_the_preview_while_suggestions_are_pending(store, cfg_for, monkeypatch):
    d, (a, b, c), cfg = _mixed(store, cfg_for)
    ins = [s["id"] for s in store.list_suggestions(d["id"]) if s["kind"] == "insert"]
    seen = []

    class FakeRenderer:
        def call(self, value):
            return value

        def screenshot_pages(self, doc_id, pages=None, max_pages=4, preview=False):
            seen.append({"pages": pages, "preview": preview})
            return [{"page": 1, "path": "p1.jpg", "total": 1, "more": [],
                     "blocks": [ins[0], ins[1], b["id"], a["id"]] if preview else [a["id"], b["id"], c["id"]]}]

    from langstage_codraft import render

    monkeypatch.setattr(render, "get_renderer", lambda: FakeRenderer())
    text, art = view_pages.func(config=cfg)
    assert seen[-1]["preview"] is True and art == {"images": ["p1.jpg"]}
    assert "PREVIEW" in text and "5 pending suggestion(s)" in text
    assert f"{ins[0]} (insert)" in text and f"{b['id']} (edit)" in text and f"{a['id']} (move)" in text
    assert f"Left out of the preview by a pending delete: {c['id']}" in text
    text, _ = view_pages.func(pending=False, config=cfg)
    assert seen[-1]["preview"] is False and "PREVIEW" not in text
    store.accept_all(d["id"])
    text, _ = view_pages.func(config=cfg)
    assert seen[-1]["preview"] is False and "PREVIEW" not in text  # nothing pending: the plain render


def test_rest_preview_serves_the_document_with_pending_suggestions_applied(tmp_path, monkeypatch):
    from langstage_codraft.agent import build_demo_agent
    from langstage_codraft.config import CodraftConfig
    from langstage_codraft.server import create_app
    from langstage_codraft.store import Store

    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8797}, use_toml=False)
    store = Store(tmp_path / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []}, store=store)
    with TestClient(app) as client:
        did = client.post("/api/docs", json={"title": "P"}).json()["id"]
        blk = client.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Old."}}).json()
        tcfg = {"configurable": {"thread_id": did}}
        call(propose_edit, tcfg, block_id=blk["id"], content="New.")
        call(propose_insert, tcfg, block_type="text", content="Added.")
        plain = client.get(f"/api/docs/{did}").json()
        assert [b["content"]["markdown"] for b in plain["blocks"]] == ["Old."] and len(plain["suggestions"]) == 2
        pv = client.get(f"/api/docs/{did}?preview=pending").json()
        assert [b["content"]["markdown"] for b in pv["blocks"]] == ["New.", "Added."]
        assert pv["suggestions"] == [] and pv["preview"]["pending"] == 2
        assert pv["blocks"][1]["id"].startswith("sug_")
        assert client.get(f"/api/docs/{did}").json()["rev"] == plain["rev"]  # the preview wrote nothing


# ------------------------------------------------------ duplicates and no-op suggestions
def test_a_no_op_edit_is_refused_with_a_clear_message(store, cfg_for):
    d, (a, b, c) = doc3(store)
    out = call(propose_edit, cfg_for(d["id"]), block_id=a["id"], content="First.")
    assert out.startswith("No change:") and "no suggestion was recorded" in out
    assert store.list_suggestions(d["id"]) == []
    with pytest.raises(NoChange):
        store.propose(d["id"], "edit", {"content": {"markdown": "First."}}, block_id=a["id"])


def test_repeating_a_pending_suggestion_records_nothing(store, cfg_for):
    d, (a, b, c) = doc3(store)
    cfg = cfg_for(d["id"])
    assert "waiting for the author" in call(propose_edit, cfg, block_id=a["id"], content="First, better.")
    again = call(propose_edit, cfg, block_id=a["id"], content="First, better.")
    assert again.startswith("Already proposed:") and "do not send it again" in again
    call(propose_delete, cfg, block_id=b["id"])
    assert call(propose_delete, cfg, block_id=b["id"]).startswith("Already proposed:")
    call(propose_move, cfg, block_id=c["id"], after_block_id=None)
    assert call(propose_move, cfg, block_id=c["id"], after_block_id=None).startswith("Already proposed:")
    call(propose_insert, cfg, block_type="text", content="A new paragraph.", after_block_id=a["id"])
    again = call(propose_insert, cfg, block_type="text", content="A new paragraph.", after_block_id=a["id"])
    assert again.startswith("Already proposed:") and "edit sug_" in again
    assert len(store.list_suggestions(d["id"])) == 4
    # a different edit still replaces the pending one, as before
    call(propose_edit, cfg, block_id=a["id"], content="First, better still.")
    edits = [s for s in store.list_suggestions(d["id"]) if s["kind"] == "edit"]
    assert len(edits) == 1 and edits[0]["payload"]["content"]["markdown"] == "First, better still."


def test_a_move_to_where_the_block_already_is_is_refused(store, cfg_for):
    d, (a, b, c) = doc3(store)
    cfg = cfg_for(d["id"])
    assert call(propose_move, cfg, block_id=b["id"], after_block_id=a["id"]).startswith("No change:")
    assert call(propose_move, cfg, block_id=a["id"], after_block_id=None).startswith("No change:")
    assert store.list_suggestions(d["id"]) == []


def test_repeated_dividers_and_page_breaks_are_not_duplicates(store, cfg_for):
    d, _ = doc3(store)
    out = call(insert_blocks, cfg_for(d["id"]), blocks=[{"type": "divider"}, {"type": "text", "content": "x"},
                                                        {"type": "divider"}, {"type": "page-break"},
                                                        {"type": "page-break"}])
    assert out.count("waiting for approval") == 5


def test_a_pending_insert_can_be_revised_restyled_and_withdrawn(store, cfg_for):
    d, (a, b, c) = doc3(store)
    cfg = cfg_for(d["id"])
    call(insert_blocks, cfg, blocks=[{"type": "text", "content": "Draft one."}, {"type": "text", "content": "Draft two."}],
         after_block_id=a["id"])
    s1, s2 = (s["id"] for s in store.list_suggestions(d["id"]))
    out = call(propose_edit, cfg, block_id=s1, content="Draft one, fixed.")
    assert out.startswith("Revised your pending insert")
    assert call(propose_edit, cfg, block_id=s1, content="Draft one, fixed.").startswith("No change:")
    out = call(set_block_layout, cfg, block_id=s1, full_width=True, variant="lead")
    assert "pending insert" in out and '"variant": "lead"' in out
    assert call(set_block_layout, cfg, block_id=s1, variant="nope").startswith("ERROR")
    assert call(propose_delete, cfg, block_id=s2).startswith("Withdrew")
    assert [s["id"] for s in store.list_suggestions(d["id"])] == [s1]
    store.accept_all(d["id"])
    blocks = store.list_blocks(d["id"])
    assert [b["content"]["markdown"] for b in blocks] == ["First.", "Draft one, fixed.", "Second.", "Third."]
    assert blocks[1]["layout"]["span"] == "full" and blocks[1]["layout"]["variant"] == "lead"
    # a withdrawn draft is not something the author rejected
    assert store.changes_since(d["id"], 0)["rejected"] == []


def test_a_pending_figure_can_be_redrawn(store, cfg_for):
    d, (a, b, c) = doc3(store)
    cfg = cfg_for(d["id"])
    svg = '<svg viewBox="0 0 10 10"><rect width="10" height="10" fill="#eee"/></svg>'
    out = call(draw_figure, cfg, svg=svg, title="Flow")
    assert "view_pages" in out
    sid = store.list_suggestions(d["id"])[0]["id"]
    first = store.get_suggestion(sid)["payload"]["content"]["src"]
    out = call(draw_figure, cfg, svg=svg.replace("#eee", "#ddd"), title="Flow", block_id=sid)
    assert out.startswith("Redrew your pending figure")
    again = store.list_suggestions(d["id"])
    assert len(again) == 1 and again[0]["payload"]["content"]["src"] != first


# ------------------------------------------------------------------ ids the agent guessed
def test_wrong_ids_say_what_was_passed_and_what_to_use(store, cfg_for):
    d, (a, b, c) = doc3(store)
    cfg = cfg_for(d["id"])
    call(propose_edit, cfg, block_id=a["id"], content="First, v2.")
    edit = store.list_suggestions(d["id"])[0]["id"]
    out = call(propose_edit, cfg, block_id=edit, content="x")
    assert out.startswith("ERROR") and f"pending edit of block {a['id']}" in out and f"use {a['id']}" in out
    typo = a["id"][:-1] + ("0" if a["id"][-1] != "0" else "1")
    out = call(propose_edit, cfg, block_id=typo, content="x")
    assert f"no block {typo}" in out and a["id"] in out and "copy them from get_document" in out
    cmt = store.add_comment(d["id"], "tighten", block_id=a["id"])
    assert "is a comment id, not a block id" in call(propose_delete, cfg, block_id=cmt["id"])
    store.delete_block(c["id"])
    assert f"block {c['id']} was deleted" in call(set_block_layout, cfg, block_id=c["id"], full_width=True)
    assert f"block {c['id']} was deleted" in call(read_blocks, cfg, block_ids=[c["id"]])
    assert "ERROR" in call(get_block_history, cfg, block_id="blk_nope")
    # an accepted insert's suggestion id points at its block
    call(propose_insert, cfg, block_type="text", content="Added.")
    ins = [s for s in store.list_suggestions(d["id"]) if s["kind"] == "insert"][0]["id"]
    new = store.accept_suggestion(ins)["payload"]["result"]["block_id"]
    out = call(propose_edit, cfg, block_id=ins, content="Added, v2.")
    assert "already accepted" in out and new in out


def test_a_block_of_another_document_is_still_refused(store, cfg_for):
    d, (a, b, c) = doc3(store)
    other = store.create_document("Other")
    assert "is not in document" in call(propose_edit, cfg_for(other["id"]), block_id=a["id"], content="x")


# ------------------------------------------------------------------- headings and Markdown
def test_heading_levels_can_be_set_the_markdown_way_or_by_argument(store, cfg_for):
    assert parse_content("heading", "### Method") == {"text": "Method", "level": 3}
    assert parse_content("heading", "Method") == {"text": "Method"}
    d, _ = doc3(store)
    cfg = cfg_for(d["id"])
    call(propose_insert, cfg, block_type="heading", content="# Top")
    call(propose_insert, cfg, block_type="heading", content="Plain")
    call(propose_insert, cfg, block_type="heading", content="Deep", heading_level=3)
    store.accept_all(d["id"])
    heads = [b for b in store.list_blocks(d["id"]) if b["type"] == "heading"]
    assert [(h["content"]["text"], h["content"]["level"]) for h in heads] == [("Top", 1), ("Plain", 2), ("Deep", 3)]
    call(propose_edit, cfg, block_id=heads[0]["id"], content="Top", heading_level=2)
    store.accept_all(d["id"])
    assert store.get_block(heads[0]["id"])["content"] == {"text": "Top", "level": 2}


def test_the_prompt_and_tools_state_the_house_rules():
    for needle in ("calculate", "<sup>2</sup>", "one text block per paragraph", "PENDING",
                   "Never delete or shorten content to solve a layout problem",
                   "throughout", "Never write about\n  the editing in it", "[Codraft note"):
        assert needle in CODRAFT_PROMPT, needle
    assert "<sup>2</sup>" in propose_edit.description and "LaTeX" in propose_edit.description
    assert "one text block per paragraph" in insert_blocks.description
    assert "Never delete or cut content, yours or the\nauthor's" in propose_delete.description
    assert "Not for routine edits" in get_block_history.description
    assert "At most 4" in view_pages.description and "PREVIEW" in view_pages.description


# ------------------------------------------------------------------------------ view_pages
class _Shots:
    def __init__(self, total):
        self.total, self.calls = total, []

    def call(self, value):
        return value

    def screenshot_pages(self, doc_id, pages=None, max_pages=4, preview=False):
        self.calls.append(pages)
        asked = [p for p in (pages or range(1, self.total + 1)) if 1 <= p <= self.total]
        return [{"page": p, "path": f"p{p}.jpg", "total": self.total, "more": asked[max_pages:], "blocks": []}
                for p in asked[:max_pages]]


def test_view_pages_says_which_pages_it_left_out_and_how_to_get_them(store):
    d, _ = doc3(store)
    r = _Shots(9)
    text, shots = page_shots(r, d["id"])
    assert [s["page"] for s in shots] == [1, 2, 3, 4]
    assert "Rendered pages 1, 2, 3, 4 of 9." in text and "NOT shown: pages 5-9" in text and "from_page=5" in text
    text, shots = page_shots(r, d["id"], from_page=5)
    assert [s["page"] for s in shots] == [5, 6, 7, 8] and "NOT shown: page 9" in text and "from_page=9" in text
    text, shots = page_shots(r, d["id"], from_page=9)
    assert [s["page"] for s in shots] == [9] and "NOT shown" not in text
    text, shots = page_shots(r, d["id"], pages=[2, 3, 5, 7, 8, 9])
    assert [s["page"] for s in shots] == [2, 3, 5, 7] and "NOT shown: pages 8-9" in text
    text, shots = page_shots(_Shots(3), d["id"])
    assert "Rendered pages 1, 2, 3 of 3." in text and "NOT shown" not in text
    text, shots = page_shots(r, d["id"], from_page=40)
    assert shots == [] and "No page 40" in text


# ------------------------------------------------------------------------------- calculate
def test_calculate_does_the_arithmetic_a_table_needs():
    out = calculate.invoke({"expressions": [
        "y1 = 5.2 * 1.04", "y2 = y1 * 1.04", "total = sum(5.2, y1, y2)", "round(total, 2)",
        "1,250,000 * 2", "max(1,200)", "15% * 200", "10 % 3", "2^10", "$4.5 + 0.1 + 0.2",
        "(27.68 - 25.1) / 25.1 * 100", "mean([1, 2, 3, 6])"]}).splitlines()
    assert out == ["y1 = 5.408", "y2 = 5.62432", "total = 16.23232", "round(total, 2) = 16.23",
                   "1,250,000 * 2 = 2500000", "max(1,200) = 200", "15% * 200 = 30", "10 % 3 = 1", "2^10 = 1024",
                   "$4.5 + 0.1 + 0.2 = 4.8", "(27.68 - 25.1) / 25.1 * 100 = 10.2788844622", "mean([1, 2, 3, 6]) = 3"]


@pytest.mark.parametrize("expr", ["__import__('os').system('echo hi')", "(1).real", "'a' * 3", "open('x')",
                                  "[x for x in (1, 2)]", "lambda: 1", "9**9**9", "1/0", "a.b", "x = y = 2",
                                  "sum", "1 if 2 else 3", "f'{1}'", "2 ** 5000"])
def test_calculate_only_does_arithmetic(expr):
    out = calculate.invoke({"expressions": [expr]})
    assert "ERROR" in out, out


def test_calculate_is_part_of_the_toolset_but_needs_no_document():
    from langstage_codraft.tools import CODRAFT_TOOLS

    assert calculate in CODRAFT_TOOLS
    assert calculate.invoke({"expressions": []}).startswith("Nothing to calculate")


# ---------------------------------------------------------------- what changed between turns
def test_changes_since_lists_what_the_author_did_not_what_the_agent_did(store, cfg_for):
    import time

    d, (a, b, c) = doc3(store)
    cfg = cfg_for(d["id"])
    call(propose_edit, cfg, block_id=a["id"], content="First, v2.")
    call(propose_insert, cfg, block_type="text", content="Added.")
    call(propose_edit, cfg, block_id=b["id"], content="Second, v2.")
    edit_a, ins, edit_b = (s["id"] for s in store.list_suggestions(d["id"]))
    since = time.time()
    time.sleep(0.02)
    new_blk = store.accept_suggestion(ins)["payload"]["result"]["block_id"]
    store.accept_suggestion(edit_a)
    store.reject_suggestion(edit_b, "keep my wording")
    store.edit_block(c["id"], content={"markdown": "Third, by the author."})
    mine = store.insert_block(d["id"], "text", {"markdown": "The author's own paragraph."})
    cmt = store.add_comment(d["id"], "shorter please", block_id=a["id"])
    gone = store.insert_block(d["id"], "text", {"markdown": "Temp."})
    store.delete_block(gone["id"])
    ch = store.changes_since(d["id"], since)
    assert [(x["id"], x["block_id"]) for x in ch["accepted"]] == [(ins, new_blk), (edit_a, a["id"])]
    assert [(x["id"], x["reason"]) for x in ch["rejected"]] == [(edit_b, "keep my wording")]
    assert ch["edited"] == [c["id"]] and ch["added"] == [mine["id"]] and ch["deleted"] == [gone["id"]]
    assert ch["comments"] == [{"id": cmt["id"], "block_id": a["id"]}]
    note = changes_note(d["id"], since)
    assert note.startswith("[Codraft note, not written by the author] Since your last turn:")
    for needle in (f"{ins} (insert -> {new_blk})", f"REJECTED by the author: {edit_b}", "keep my wording",
                   f"edited by the author (re-read before changing them): {c['id']}",
                   f"added by the author: {mine['id']}", f"deleted by the author (these ids no longer exist): {gone['id']}",
                   f"new open comments: {cmt['id']} on {a['id']}"):
        assert needle in note, needle
    assert changes_note(d["id"], time.time()) == ""  # nothing since: no note


def test_auto_applied_suggestions_are_not_news(store, cfg_for):
    d = store.create_document("Auto", {"approval": "auto"})
    call(propose_insert, cfg_for(d["id"]), block_type="text", content="Applied at once.")
    ch = store.changes_since(d["id"], 0)
    assert all(not v for v in ch.values()), ch


class _Request:
    def __init__(self, messages, system=None):
        self.messages, self.system_message = messages, system
        self.overrides: dict = {}

    def override(self, **kw):
        new = _Request(kw.get("messages", self.messages), kw.get("system_message", self.system_message))
        new.overrides = kw
        return new


def _prepare(mw, doc_id, messages, monkeypatch):
    import langgraph.config

    monkeypatch.setattr(langgraph.config, "get_config", lambda: {"configurable": {"thread_id": doc_id}})
    return mw._prepare(_Request(messages))


def test_each_turn_starts_with_what_the_author_changed(store, cfg_for, monkeypatch):
    import time

    d, (a, b, c) = doc3(store)
    cfg = cfg_for(d["id"])
    mw = CodraftMiddleware(attach_images=False)
    turn1 = [HumanMessage(content="Draft something.", id="h1")]
    out = _prepare(mw, d["id"], turn1, monkeypatch)
    assert [m.content for m in out.messages] == ["Draft something."]  # the first turn has nothing to report
    call(propose_edit, cfg, block_id=a["id"], content="First, v2.")
    sid = store.list_suggestions(d["id"])[0]["id"]
    turn1 += [AIMessage(content="", tool_calls=[{"name": "propose_edit", "args": {}, "id": "c1"}]),
              ToolMessage(content="ok", tool_call_id="c1")]
    assert len(_prepare(mw, d["id"], turn1, monkeypatch).messages) == 3  # same turn: still no note
    time.sleep(0.02)
    store.reject_suggestion(sid, "too formal")
    store.edit_block(b["id"], content={"markdown": "Second, by the author."})
    turn2 = turn1 + [AIMessage(content="Done."), HumanMessage(content="Now fix the intro.", id="h2")]
    out = _prepare(mw, d["id"], turn2, monkeypatch)
    assert len(out.messages) == len(turn2) + 1
    note = out.messages[-2]
    assert isinstance(note, HumanMessage) and note.content.startswith("[Codraft note")
    assert f"REJECTED by the author: {sid}" in note.content and "too formal" in note.content and b["id"] in note.content
    assert out.messages[-1].content == "Now fix the intro."  # the request is still what the model reads last
    # later model calls of the same turn get the same note, even though more has happened since
    store.edit_block(c["id"], content={"markdown": "Third, by the author."})
    turn2b = turn2 + [AIMessage(content="", tool_calls=[{"name": "get_document", "args": {}, "id": "c2"}]),
                      ToolMessage(content="outline", tool_call_id="c2")]
    out2 = _prepare(mw, d["id"], turn2b, monkeypatch)
    assert out2.messages[len(turn2) - 1].content == note.content and out2.messages[-1].content == "outline"
    assert len(turn2b) == len(turn2) + 2  # the thread itself is never changed
    # a turn after which the author did nothing gets no note
    turn3 = turn2b + [AIMessage(content="Fixed."), HumanMessage(content="Thanks, one more.", id="h3")]
    store._agent_turns[d["id"]]["last_call"] = time.time()
    assert len(_prepare(mw, d["id"], turn3, monkeypatch).messages) == len(turn3)
    assert len(_prepare(CodraftMiddleware(attach_images=False, turn_notes=False), d["id"], turn2, monkeypatch).messages) == len(turn2)


def test_a_thread_that_is_not_a_document_gets_no_note(store, monkeypatch):
    mw = CodraftMiddleware(attach_images=False)
    msgs = [HumanMessage(content="hi", id="h1")]
    assert len(_prepare(mw, "not-a-doc", msgs, monkeypatch).messages) == 1
    assert "not-a-doc" not in store._agent_turns


# ------------------------------------------------------------- model defaults and research
def test_the_default_model_has_an_output_cap_and_reasoning(monkeypatch):
    from langstage_codraft import agent

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    model, plain, notes = agent.make_models("openrouter:anthropic/claude-haiku-5.5")
    assert model.max_tokens == 16000 and model.extra_body == {"reasoning": {"effort": "low"}}
    assert plain.max_tokens == 16000 and not plain.extra_body and notes == []
    model, plain, _ = agent.make_models("openrouter:anthropic/claude-haiku-5.5", max_tokens=4000, reasoning="high")
    assert model.max_tokens == 4000 and model.extra_body == {"reasoning": {"effort": "high"}}
    model, plain, _ = agent.make_models("openrouter:anthropic/claude-haiku-5.5", max_tokens=0, reasoning="off")
    assert model.max_tokens is None and plain is None and not model.extra_body
    # GPT-5 and o-series: the Responses API, the only place GPT-5.4+ reason together with tools
    for name in ("gpt-5-mini", "gpt-5.4", "o4-mini"):
        assert agent.reasoning_kwargs(f"openai:{name}", "auto") == {
            "use_responses_api": True, "reasoning": {"effort": "low"}}
    assert agent.reasoning_kwargs("openai:gpt-5.4", "high") == {
        "use_responses_api": True, "reasoning": {"effort": "high"}}
    assert agent.reasoning_kwargs("openai:gpt-5.4", "off") == {}
    assert agent.reasoning_kwargs("openai:gpt-4.1-mini", "high") == {}
    model, plain, notes = agent.make_models("openai:gpt-5-mini")
    assert model.use_responses_api is True and model.reasoning == {"effort": "low"} and model.max_tokens == 16000
    assert model.reasoning_effort is None and notes == []
    # the fallback model is plain Chat Completions: no Responses API, no reasoning
    assert not plain.use_responses_api and plain.reasoning is None and plain.reasoning_effort is None
    assert plain.max_tokens == 16000
    model, plain, _ = agent.make_models("openai:gpt-4.1-mini")  # no reasoning switch: just the cap
    assert plain is None and model.max_tokens == 16000 and model.reasoning_effort is None
    assert agent.make_model("openai:gpt-4.1-mini").max_tokens == 16000
    assert agent.reasoning_kwargs("anthropic:claude-haiku-5-5", "auto", 16000) == {
        "thinking": {"type": "enabled", "budget_tokens": 2048}}
    assert agent.reasoning_kwargs("anthropic:claude-haiku-5-5", "high", 4000) == {
        "thinking": {"type": "enabled", "budget_tokens": 2976}}
    assert agent.reasoning_kwargs("anthropic:claude-haiku-5-5", "high", 1500) == {}
    _, _, notes = agent.make_models("openai:gpt-4.1-mini", reasoning="high")
    assert notes and "no effect" in notes[0]


def test_a_provider_class_that_refuses_the_settings_still_yields_a_model(monkeypatch):
    from langstage_codraft import agent

    calls = []

    def construct(spec, **kw):
        calls.append(kw)
        if kw:
            raise TypeError(f"unexpected keyword {sorted(kw)}")
        return "bare-model"

    monkeypatch.setattr(agent, "_construct", construct)
    model, plain, notes = agent.make_models("openai:gpt-5-mini")
    assert model == "bare-model" and plain is None
    assert any("refused max_tokens" in n for n in notes) and any("reasoning is off" in n for n in notes)


class _Boom(Exception):
    def __init__(self, msg, status_code):
        super().__init__(msg)
        self.status_code = status_code


def test_reasoning_is_dropped_not_fatal_when_the_provider_refuses_it():
    from langstage_codraft.agent import ReasoningFallback, rejects_reasoning

    refusal = _Boom("Error code: 400 - Function tools with reasoning_effort are not supported for gpt-5.4 in "
                    "/v1/chat/completions.", 400)
    assert rejects_reasoning(refusal)
    assert rejects_reasoning(_Boom("Unsupported parameter: 'thinking'", 422))
    assert not rejects_reasoning(_Boom("context length exceeded", 400))
    assert not rejects_reasoning(_Boom("reasoning quota exceeded", 429))
    assert not rejects_reasoning(RuntimeError("thinking failed"))
    # an account or a gateway without the Responses API (what GPT-5 and o-series now use)
    assert rejects_reasoning(_Boom("Error code: 404 - Not Found", 404))
    assert rejects_reasoning(_Boom("Method Not Allowed", 405))
    assert rejects_reasoning(_Boom("Invalid URL (POST /v1/responses)", 400))
    assert rejects_reasoning(_Boom("The Responses API is not enabled for this organization", 400))
    assert not rejects_reasoning(_Boom("Incorrect API key provided", 401))
    assert not rejects_reasoning(_Boom("Rate limit reached for responses", 429))

    class Req:
        def __init__(self, model="reasoning"):
            self.model = model

        def override(self, model):
            return Req(model)

    seen = []

    def handler(req):
        seen.append(req.model)
        if req.model == "reasoning":
            raise refusal
        return "ok"

    fb = ReasoningFallback("plain", "openai:gpt-5.4")
    assert fb.wrap_model_call(Req(), handler) == "ok" and seen == ["reasoning", "plain"]
    assert fb.degraded and fb.wrap_model_call(Req(), handler) == "ok" and seen[-1] == "plain" and len(seen) == 3

    def broken(req):
        raise _Boom("context length exceeded", 400)

    fb2 = ReasoningFallback("plain")
    with pytest.raises(_Boom):
        fb2.wrap_model_call(Req(), broken)
    assert not fb2.degraded

    # no Responses API: the turn carries on over Chat Completions, and stays there
    seen.clear()

    def no_responses_api(req):
        seen.append(req.model)
        if req.model == "reasoning":
            raise _Boom("Error code: 404 - Not Found", 404)
        return "ok"

    fb3 = ReasoningFallback("plain", "openai:gpt-5.4")
    assert fb3.wrap_model_call(Req(), no_responses_api) == "ok" and seen == ["reasoning", "plain"]
    assert fb3.wrap_model_call(Req(), no_responses_api) == "ok" and seen == ["reasoning", "plain", "plain"]

    # an unknown model fails on the plain model too, and that error is the one raised
    def unknown_model(req):
        raise _Boom(f"The model does not exist ({req.model})", 404)

    fb4 = ReasoningFallback("plain")
    with pytest.raises(_Boom, match="plain"):
        fb4.wrap_model_call(Req(), unknown_model)


async def test_reasoning_fallback_works_for_async_model_calls():
    from langstage_codraft.agent import ReasoningFallback

    class Req:
        def __init__(self, model="reasoning"):
            self.model = model

        def override(self, model):
            return Req(model)

    async def handler(req):
        if req.model == "reasoning":
            raise _Boom("thinking is not supported by this model", 400)
        return "ok"

    fb = ReasoningFallback("plain")
    assert await fb.awrap_model_call(Req(), handler) == "ok" and fb.degraded
    assert await fb.awrap_model_call(Req(), handler) == "ok"


def test_the_default_agent_is_built_with_the_cap_and_the_fallback(monkeypatch):
    from langstage_codraft import agent
    from langstage_codraft.config import CodraftConfig

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    built = {}
    real = agent.make_models

    def spy(spec, **kw):
        built.update(spec=spec, **kw)
        return real(spec, **kw)

    monkeypatch.setattr(agent, "make_models", spy)
    cfg = CodraftConfig.resolve(overrides={"max_output_tokens": 8000, "reasoning": "medium"}, use_toml=False)
    graph, info = agent.resolve(cfg)
    assert built == {"spec": "openrouter:anthropic/claude-haiku-5.5", "max_tokens": 8000, "reasoning": "medium"}
    assert info["max_output_tokens"] == 8000 and info["reasoning"] == "medium" and info["kind"] == "default"
    assert "calculate" in agent.graph_tool_names(graph) and agent.missing_tools(graph) == []
    cfg = CodraftConfig.resolve(overrides={"reasoning": "off"}, use_toml=False)
    assert agent.resolve(cfg)[1]["reasoning"] == "off"


def test_model_settings_come_through_the_config_chain(monkeypatch, tmp_path):
    from langstage_codraft.config import CodraftConfig

    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(use_toml=False)
    assert (cfg.max_output_tokens, cfg.reasoning) == (16000, "auto")
    (tmp_path / "langstage.toml").write_text('[codraft]\nmax_output_tokens = 6000\nreasoning = "HIGH"\n')
    cfg = CodraftConfig.resolve()
    assert (cfg.max_output_tokens, cfg.reasoning) == (6000, "high")
    monkeypatch.setenv("LANGSTAGE_CODRAFT_REASONING", "none")
    monkeypatch.setenv("LANGSTAGE_CODRAFT_MAX_OUTPUT_TOKENS", "0")
    cfg = CodraftConfig.resolve()
    assert (cfg.max_output_tokens, cfg.reasoning) == (0, "off")
    monkeypatch.setenv("LANGSTAGE_CODRAFT_REASONING", "banana")  # degrades with a note, never raises
    monkeypatch.setenv("LANGSTAGE_CODRAFT_MAX_OUTPUT_TOKENS", "12")
    cfg = CodraftConfig.resolve()
    assert (cfg.max_output_tokens, cfg.reasoning) == (6000, "high")


def _pdf_bytes(text):
    """A one-page PDF with a line of text (hand-built, so the test needs no PDF writer)."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"


def test_fetch_url_reads_pdfs_and_decodes_entities():
    from langstage_codraft.agent import readable_text

    pdf = _pdf_bytes("Cool roofs lower peak demand")
    out = readable_text("https://example.org/paper", "application/pdf", pdf)
    assert "Cool roofs lower peak demand" in out and "%PDF" not in out
    assert "Cool roofs" in readable_text("https://example.org/paper.pdf", "application/octet-stream", pdf)
    bad = readable_text("https://example.org/scan.pdf", "application/pdf", b"%PDF-1.4 not really a pdf")
    assert "can't be read here" in bad and "%PDF" not in bad
    html = b"<html><body><p>Caf&eacute; &amp; bar &#8212; 5&nbsp;&deg;C</p><script>x()</script></body></html>"
    assert readable_text("https://example.org", "text/html; charset=utf-8", html) == "Café & bar — 5 °C"
    long = ("<html><body>" + "word " * 4000 + "</body></html>").encode()
    out = readable_text("https://example.org", "text/html", long, max_chars=1000)
    assert out.startswith("[page, characters 0-1000 of ") and "offset=1000" in out
    assert readable_text("https://example.org", "text/html", long, max_chars=1000, offset=1000).startswith(
        "[page, characters 1000-2000 of ")
    assert "not text" in readable_text("https://example.org/a.zip", "application/zip", b"PK\x03\x04")


def test_a_failed_web_search_says_what_to_do_instead(monkeypatch):
    import sys
    import time
    import types

    from langstage_codraft import agent

    calls = []

    class DDGS:
        def text(self, query, max_results=5):
            calls.append(query)
            if len(calls) < 3:
                raise RuntimeError("202 Ratelimit")
            return [] if query == "nothing" else [{"title": "T", "href": "https://example.org", "body": "B"}]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=DDGS))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    search = next(t for t in agent.research_tools() if t.name == "web_search")
    out = search.invoke({"query": "cool roofs"})
    assert len(calls) == 2 and "failed twice" in out and "Do not keep retrying" in out and "fetch_url" in out
    assert "https://example.org" in search.invoke({"query": "cool roofs"})
    assert "No results for 'nothing'" in search.invoke({"query": "nothing"})


def test_store_refuses_nonsense_anchors(store):
    d, (a, b, c) = doc3(store)
    with pytest.raises(NotFound):
        store.propose(d["id"], "insert", {"type": "text", "content": {"markdown": "x"}, "after": "sug_missing"})
    e = store.propose(d["id"], "edit", {"content": {"markdown": "First, v2."}}, block_id=a["id"])
    with pytest.raises(Invalid):
        store.propose(d["id"], "insert", {"type": "text", "content": {"markdown": "x"}, "after": e["id"]})
    ins = store.propose(d["id"], "insert", {"type": "text", "content": {"markdown": "y"}, "after": a["id"]})
    store.reject_suggestion(ins["id"])
    with pytest.raises(Invalid):
        store.propose(d["id"], "insert", {"type": "text", "content": {"markdown": "z"}, "after": ins["id"]})
