"""Document checkpoints: what changed since a point in time, and going back to it (gh #61).

A checkpoint records, for every content block, the version that was showing and where the
block stood. From it the store says what was edited, added, deleted and moved since, and
puts the content back, all of it or chosen blocks, without overwriting anything. One is
taken before each agent turn that proposes content, before each Accept all and before a
restore; the author names their own.

The editor and a real-model round trip are in tests/e2e/test_checkpoints_61.py.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from langstage_codraft import checkpoints as ck
from langstage_codraft import store as store_mod
from langstage_codraft import tools as tools_mod
from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.mcp_server import build_mcp_server
from langstage_codraft.middleware import CODRAFT_PROMPT
from langstage_codraft.server import create_app
from langstage_codraft.store import Invalid, NotFound, Store
from langstage_codraft.tools import (
    CODRAFT_TOOLS,
    begin_turn,
    changes_since,
    list_checkpoints,
    propose_delete,
    propose_edit,
    propose_insert,
    propose_move,
    restore_block,
)


@pytest.fixture
def clock(monkeypatch):
    """The store's clock, by hand."""
    now = [1_000_000.0]
    monkeypatch.setattr(store_mod.time, "time", lambda: now[0])

    def tick(seconds: float = 60.0) -> float:
        now[0] += seconds
        return now[0]

    return tick


def _doc(store, n=5):
    d = store.create_document("Report")
    blocks = [store.insert_block(d["id"], "text", {"markdown": f"Paragraph {i} as first written."}, author="Dana")
              for i in range(n)]
    return d, blocks


def _state(store, doc_id):
    """The content, as a reader sees it: every block's id and text, in order."""
    return [(b["id"], b["content"].get("markdown", b["content"].get("text"))) for b in store.list_blocks(doc_id)
            if b["type"] != "spacer"]


def _counts(store, doc_id, cid):
    return {k: v for k, v in store.checkpoint_changes(doc_id, cid)["counts"].items() if k != "total"}


# ------------------------------------------------------------------ pure helpers
def test_moved_is_the_smallest_set_of_blocks_that_changed_place():
    assert ck.moved_ids(list("ABCDE"), list("ABCDE")) == []
    assert ck.moved_ids(list("ABC"), list("CAB")) == ["C"]            # one block went to the front
    assert len(ck.moved_ids(list("ABCDE"), list("ABDCE"))) == 1       # a swap moves one block, not two
    assert ck.moved_ids(list("ABCE"), list("ABXCE")) == []            # an insert moves nothing
    assert ck.moved_ids(list("ABCE"), list("ACE")) == []              # nor does a delete
    assert ck.moved_ids(list("ABCDE"), list("EDCBA")) == list("BCDE") or len(ck.moved_ids(list("ABCDE"), list("EDCBA"))) == 4
    assert ck.moved_ids([], []) == []


def test_the_word_diff_marks_what_went_and_what_came_and_stays_short():
    d = ck.word_diff("Revenue grew four percent in the third quarter on strong demand.",
                     "Revenue grew six percent in the third quarter on strong demand, mostly abroad.")
    assert "[-four-]" in d and "{+six+}" in d and "{+demand, mostly abroad.+}" in d
    assert ck.word_diff("same text", "same  text") == ""
    long = ck.word_diff("old " * 400, "new " * 400, budget=200)
    assert len(long) < 260 and long.endswith("(more changes: read the block)")


def test_labels_say_what_the_checkpoint_was_taken_before():
    assert ck.auto_label("accept-all", "12") == "Before Accept all (12 suggestions)"
    assert ck.auto_label("accept-all", "1") == "Before Accept all (1 suggestion)"
    assert ck.auto_label("turn", agent_name="Co-author") == "Before Co-author's turn"
    assert ck.auto_label("restore", "Sent to legal") == "Before going back to “Sent to legal”"
    assert ck.auto_label("restore") == "Before undoing a restore"
    assert ck.clean_name("  Sent\n to   legal  ") == "Sent to legal" and len(ck.clean_name("x" * 300)) == ck.NAME_MAX


# --------------------------------------------------------------- reconstruction
def test_a_new_checkpoint_has_no_changes_and_names_who_took_it(store, clock):
    d, _ = _doc(store)
    c = store.create_checkpoint(d["id"], "  Sent to   legal ", by="Dana")
    assert c["id"].startswith("chk_") and c["name"] == "Sent to legal" and c["kind"] == "named"
    assert c["by"] == "Dana" and c["at"] == 1_000_000.0 and c["blocks"] == 5
    ch = store.checkpoint_changes(d["id"], c["id"])
    assert ch["counts"] == {"edited": 0, "added": 0, "deleted": 0, "moved": 0, "total": 0}
    assert ch["words_before"] == ch["words_after"] == 25
    assert store.list_checkpoints(d["id"])[0]["changes"]["total"] == 0
    with pytest.raises(Invalid, match="needs a name"):
        store.create_checkpoint(d["id"], "   ")
    with pytest.raises(NotFound):
        store.checkpoint_changes(d["id"], "chk_nope")


def test_changes_lists_what_was_edited_added_deleted_and_moved(store, clock):
    d, (a, b, c, e, f) = _doc(store)
    cp = store.create_checkpoint(d["id"], "Sent to legal")
    clock()
    store.edit_block(a["id"], content={"markdown": "Paragraph 0, rewritten at some length since."}, author="Dana")
    new = store.insert_block(d["id"], "heading", {"text": "Risks"}, after=b["id"], author="Dana")
    store.delete_block(c["id"])
    store.move_block(f["id"], None)  # the last block goes to the front
    ch = store.checkpoint_changes(d["id"], cp["id"])
    assert ch["counts"] == {"edited": 1, "added": 1, "deleted": 1, "moved": 1, "total": 4}
    [ed] = ch["edited"]
    assert ed["block_id"] == a["id"] and ed["from_version"] == a["head"] and ed["to_version"] != a["head"]
    assert ed["before"]["content"]["markdown"] == "Paragraph 0 as first written."
    assert ed["after"]["content"]["markdown"].endswith("since.") and (ed["words_before"], ed["words_after"]) == (5, 7)
    assert ed["refs_changed"] is False
    assert [x["block_id"] for x in ch["added"]] == [new["id"]] and ch["added"][0]["preview"] == "Risks"
    assert [x["block_id"] for x in ch["deleted"]] == [c["id"]]
    assert ch["deleted"][0]["content"]["markdown"] == "Paragraph 2 as first written."
    assert ch["moved"] == [{"block_id": f["id"], "type": "text", "words": 5,
                            "preview": "Paragraph 4 as first written.", "was": 5, "now": 1, "after": None}]
    assert ch["words_before"] == 25 and ch["words_after"] == 25 - 5 + 2 + 1
    assert store.list_checkpoints(d["id"])[0]["changes"] == ch["counts"]


def test_a_checkout_to_an_older_version_is_seen_though_it_writes_no_version(store, clock):
    """The case timestamps cannot answer: stepping back moves the head without a new version."""
    d, (a, *_) = _doc(store)
    v0 = a["head"]
    v1 = store.edit_block(a["id"], content={"markdown": "Second wording."}, author="Dana")["head"]
    clock()
    store.checkout(a["id"], v0)                         # the author steps back: v0 is showing
    cp = store.create_checkpoint(d["id"], "Stepped back")
    assert _counts(store, d["id"], cp["id"]) == {"edited": 0, "added": 0, "deleted": 0, "moved": 0}
    clock()
    store.checkout(a["id"], v1)                         # and forward again, with no new version
    [ed] = store.checkpoint_changes(d["id"], cp["id"])["edited"]
    assert (ed["from_version"], ed["to_version"]) == (v0, v1)
    assert ed["before"]["content"]["markdown"] == "Paragraph 0 as first written."
    store.restore_checkpoint(d["id"], cp["id"])
    assert store.get_block(a["id"])["head"] == v0       # the version that was showing, not the newest older one


def test_an_edit_back_to_the_same_words_is_not_a_change(store, clock):
    d, (a, *_) = _doc(store)
    cp = store.create_checkpoint(d["id"], "Start")
    store.edit_block(a["id"], content={"markdown": "Something else."}, author="Dana")
    store.edit_block(a["id"], content={"markdown": "Paragraph 0 as first written."}, author="Dana")
    assert store.get_block(a["id"])["head"] != a["head"]
    assert store.checkpoint_changes(d["id"], cp["id"])["counts"]["total"] == 0
    assert store.restore_checkpoint(d["id"], cp["id"]) == {
        "changed": False, "edited": 0, "restored": 0, "removed": 0, "moved": 0, "skipped": [], "undo": None, "notes": []}


def test_a_change_to_sources_alone_is_an_edit(store):
    d, (a, *_) = _doc(store)
    cp = store.create_checkpoint(d["id"], "Start")
    store.edit_block(a["id"], refs=[{"title": "Annual report", "url": "https://example.org/report"}], author="Dana")
    [ed] = store.checkpoint_changes(d["id"], cp["id"])["edited"]
    assert ed["refs_changed"] is True and ed["words_before"] == ed["words_after"]
    store.restore_checkpoint(d["id"], cp["id"])
    assert store.get_block(a["id"])["refs"] == []


def test_restore_puts_everything_back_and_overwrites_nothing(store, clock):
    d, (a, b, c, e, f) = _doc(store)
    before = _state(store, d["id"])
    cp = store.create_checkpoint(d["id"], "Sent to legal")
    clock()
    newer = store.edit_block(a["id"], content={"markdown": "Rewritten."}, author="agent")["head"]
    new = store.insert_block(d["id"], "text", {"markdown": "A new paragraph."}, after=b["id"], author="agent")
    store.delete_block(c["id"])
    store.move_block(f["id"], a["id"])
    store.move_block(e["id"], None)
    changed = _state(store, d["id"])
    assert changed != before
    clock()
    got = store.restore_checkpoint(d["id"], cp["id"], by="Dana")
    assert got["changed"] and (got["edited"], got["restored"], got["removed"]) == (1, 1, 1) and got["moved"] >= 1
    assert got["skipped"] == [] and got["undo"].startswith("chk_")
    assert _state(store, d["id"]) == before
    # Nothing was overwritten: the newer version is still in the block's tree, one step forward ...
    tree = store.versions(a["id"])
    assert tree["head"] == a["head"] and newer in {v["id"] for v in tree["versions"]}
    assert store.step(a["id"], "forward")["head"] == newer
    store.checkout(a["id"], a["head"])
    # ... the block that was added is in Recently deleted, with its text ...
    assert [x["id"] for x in store.deleted_blocks(d["id"])] == [new["id"]]
    assert store.deleted_blocks(d["id"])[0]["preview"] == "A new paragraph."
    # ... and the block that was deleted came back with its history.
    assert store.get_block(c["id"])["n_versions"] == 1
    assert store.checkpoint_changes(d["id"], cp["id"])["counts"]["total"] == 0


def test_a_restore_can_be_undone(store, clock):
    d, (a, b, c, e, f) = _doc(store)
    cp = store.create_checkpoint(d["id"], "Sent to legal")
    clock()
    store.edit_block(a["id"], content={"markdown": "Rewritten."}, author="Dana")
    new = store.insert_block(d["id"], "text", {"markdown": "A new paragraph."}, after=b["id"], author="Dana")
    store.delete_block(c["id"])
    store.move_block(f["id"], a["id"])
    changed = _state(store, d["id"])
    clock()
    got = store.restore_checkpoint(d["id"], cp["id"], by="Dana")
    undo = store.get_checkpoint(d["id"], got["undo"])
    assert undo["kind"] == "auto" and undo["reason"] == "restore" and undo["by"] == "Dana"
    assert undo["name"] == "Before going back to “Sent to legal”"
    clock()
    back = store.restore_checkpoint(d["id"], got["undo"])
    assert back["changed"] and _state(store, d["id"]) == changed
    assert store.get_block(new["id"])["content"]["markdown"] == "A new paragraph."
    assert c["id"] in {x["id"] for x in store.deleted_blocks(d["id"])}
    # And that undo can be undone in turn. Going back and forth leaves no trail: the content
    # before the undo is the checkpoint's own, so that checkpoint is what undoes it.
    assert back["undo"] == cp["id"]
    again = store.restore_checkpoint(d["id"], back["undo"])
    assert store.checkpoint_changes(d["id"], cp["id"])["counts"]["total"] == 0
    assert again["undo"] == got["undo"] and len(store.list_checkpoints(d["id"])) == 2


def test_undoing_a_restore_after_more_edits_is_checkpointed_under_a_plain_name(store, clock):
    d, (a, b, *_ ) = _doc(store)
    cp = store.create_checkpoint(d["id"], "Sent to legal")
    store.edit_block(a["id"], content={"markdown": "Rewritten."}, author="Dana")
    got = store.restore_checkpoint(d["id"], cp["id"])
    clock()
    store.edit_block(b["id"], content={"markdown": "Edited after going back."}, author="Dana")
    back = store.restore_checkpoint(d["id"], got["undo"])
    assert back["edited"] == 2 and store.get_block(a["id"])["content"]["markdown"] == "Rewritten."
    assert store.get_checkpoint(d["id"], back["undo"])["name"] == "Before undoing a restore"
    assert [c["name"] for c in store.list_checkpoints(d["id"])] == [
        "Before undoing a restore", "Before going back to “Sent to legal”", "Sent to legal"]


def test_chosen_blocks_only(store, clock):
    d, (a, b, c, e, f) = _doc(store)
    cp = store.create_checkpoint(d["id"], "Start")
    store.edit_block(a["id"], content={"markdown": "A rewritten."}, author="Dana")
    store.edit_block(b["id"], content={"markdown": "B rewritten."}, author="Dana")
    new = store.insert_block(d["id"], "text", {"markdown": "New."}, author="Dana")
    store.delete_block(c["id"])
    store.move_block(f["id"], None)
    got = store.restore_checkpoint(d["id"], cp["id"], blocks=[a["id"]])
    assert (got["edited"], got["restored"], got["removed"], got["moved"]) == (1, 0, 0, 0)
    assert store.get_block(a["id"])["content"]["markdown"] == "Paragraph 0 as first written."
    assert store.get_block(b["id"])["content"]["markdown"] == "B rewritten."
    assert _counts(store, d["id"], cp["id"]) == {"edited": 1, "added": 1, "deleted": 1, "moved": 1}
    # The moved block goes back behind the block it followed; the rest stays as it is now.
    got = store.restore_checkpoint(d["id"], cp["id"], blocks=[f["id"]])
    assert got["moved"] == 1
    order = [x["id"] for x in store.list_blocks(d["id"])]
    assert order == [a["id"], b["id"], e["id"], f["id"], new["id"]]
    got = store.restore_checkpoint(d["id"], cp["id"], blocks=[c["id"], new["id"]])
    assert (got["restored"], got["removed"]) == (1, 1)
    assert [x["id"] for x in store.list_blocks(d["id"])] == [a["id"], b["id"], c["id"], e["id"], f["id"]]
    with pytest.raises(Invalid, match="nothing to revert"):
        store.restore_checkpoint(d["id"], cp["id"], blocks=[a["id"]])
    with pytest.raises(Invalid, match="list of block ids"):
        store.restore_checkpoint(d["id"], cp["id"], blocks="all")


def test_a_block_moved_to_the_front_goes_back_in_front_of_what_it_preceded(store):
    d, (a, b, c, *_ ) = _doc(store)
    cp = store.create_checkpoint(d["id"], "Start")
    store.move_block(a["id"], "__end__")
    [m] = store.checkpoint_changes(d["id"], cp["id"])["moved"]
    assert m["block_id"] == a["id"] and (m["was"], m["now"]) == (1, 5)
    store.restore_checkpoint(d["id"], cp["id"], blocks=[a["id"]])
    assert [x["id"] for x in store.list_blocks(d["id"])][:3] == [a["id"], b["id"], c["id"]]


def test_restoring_the_order_of_a_long_document_is_exact(store):
    """Positions are put back as stored, not by nudging blocks one by one (which would run
    out of precision between two neighbours after a few dozen moves)."""
    d, blocks = _doc(store, n=120)
    before = [b["id"] for b in store.list_blocks(d["id"])]
    cp = store.create_checkpoint(d["id"], "Start")
    for b in blocks[::2]:
        store.move_block(b["id"], None)
    assert [b["id"] for b in store.list_blocks(d["id"])] != before
    got = store.restore_checkpoint(d["id"], cp["id"])
    assert got["moved"] >= 59 and [b["id"] for b in store.list_blocks(d["id"])] == before
    ords = [b["ord"] for b in store.list_blocks(d["id"])]
    assert len(set(ords)) == len(ords)


def test_layout_settings_and_reserved_areas_are_left_to_the_design_history(store, clock):
    d, (a, b, *_ ) = _doc(store)
    cp = store.create_checkpoint(d["id"], "Start")
    clock()
    store.set_layout(a["id"], {"span": "full", "variant": "lead"}, by="Dana")
    store.update_document(d["id"], settings={"columns": 2}, by="Dana")
    area = store.reserve_area(d["id"], {"page": 1, "x": 10, "y": 10, "w": 100, "h": 80}, author="agent")["block"]
    assert store.checkpoint_changes(d["id"], cp["id"])["counts"]["total"] == 0  # none of it is content
    store.edit_block(b["id"], content={"markdown": "Rewritten."}, author="Dana")
    steps = len(store.design_history(d["id"]))
    store.restore_checkpoint(d["id"], cp["id"])
    assert store.get_block(a["id"])["layout"]["variant"] == "lead"
    assert store.get_document(d["id"])["settings"]["columns"] == 2
    assert store.get_block(area["id"])["type"] == "spacer"          # the reserved area is still there
    assert len(store.design_history(d["id"])) == steps              # and the look's history is untouched


# ----------------------------------------------------------- automatic checkpoints
def test_one_checkpoint_per_agent_turn_taken_before_its_first_proposal(store, cfg_for, clock):
    d, (a, b, *_ ) = _doc(store)
    cfg = cfg_for(d["id"])
    begin_turn(d["id"], "msg-1")
    propose_edit.func(block_id=a["id"], content="Paragraph 0, tightened.", config=cfg)
    clock(5)
    propose_edit.func(block_id=b["id"], content="Paragraph 1, tightened.", config=cfg)
    propose_insert.func(block_type="text", content="A new closing paragraph.", config=cfg)
    [turn] = store.list_checkpoints(d["id"])
    assert turn["kind"] == "auto" and turn["reason"] == "turn" and turn["by"] == "agent"
    assert turn["name"] == "Before the agent's turn" and turn["at"] == 1_000_000.0
    assert turn["changes"]["total"] == 0                 # suggestions are not content until accepted
    for s in store.list_suggestions(d["id"]):
        store.accept_suggestion(s["id"])
    assert store.list_checkpoints(d["id"])[0]["changes"] == {"edited": 2, "added": 1, "deleted": 0, "moved": 0, "total": 3}
    clock()
    begin_turn(d["id"], "msg-2")
    propose_edit.func(block_id=a["id"], content="Paragraph 0, tightened again.", config=cfg)
    assert [c["reason"] for c in store.list_checkpoints(d["id"])] == ["turn", "turn"]
    # A refused proposal (nothing would change) leaves no checkpoint behind.
    d2, (x, *_ ) = _doc(store)
    begin_turn(d2["id"], "msg-9")
    out = propose_edit.func(block_id=x["id"], content="Paragraph 0 as first written.", config=cfg_for(d2["id"]))
    assert "ERROR" in out or "no change" in out.lower() or "nothing" in out.lower()
    assert store.list_checkpoints(d2["id"]) == []


def test_in_auto_mode_the_turn_checkpoint_is_the_content_before_the_turn(store, cfg_for, clock):
    d, (a, b, c, *_ ) = _doc(store)
    store.update_document(d["id"], settings={"approval": "auto"})
    before = _state(store, d["id"])
    cfg = cfg_for(d["id"])
    begin_turn(d["id"], "msg-1")
    propose_edit.func(block_id=a["id"], content="Applied at once.", config=cfg)
    propose_delete.func(block_id=c["id"], config=cfg)
    propose_move.func(block_id=b["id"], after_block_id=None, config=cfg)
    propose_insert.func(block_type="text", content="Added at once.", config=cfg)
    [turn] = store.list_checkpoints(d["id"])
    assert turn["changes"]["edited"] == 1 and turn["changes"]["added"] == 1 and turn["changes"]["deleted"] == 1
    store.restore_checkpoint(d["id"], turn["id"])
    assert _state(store, d["id"]) == before


def test_an_agent_with_no_turns_gets_one_checkpoint_per_burst(store, clock):
    d, (a, b, *_ ) = _doc(store)
    store.propose(d["id"], "edit", {"content": {"markdown": "One."}}, block_id=a["id"])
    clock(60)
    store.propose(d["id"], "edit", {"content": {"markdown": "Two."}}, block_id=b["id"])
    assert len(store.list_checkpoints(d["id"])) == 1
    for s in store.list_suggestions(d["id"]):
        store.accept_suggestion(s["id"])
    clock(ck.AGENT_FOLD_S + 1)
    store.propose(d["id"], "edit", {"content": {"markdown": "Three."}}, block_id=a["id"])
    assert len(store.list_checkpoints(d["id"])) == 2


def test_accept_all_can_be_undone_as_a_whole(store, cfg_for, clock):
    d, (a, b, c, e, f) = _doc(store)
    before = _state(store, d["id"])
    cfg = cfg_for(d["id"])
    begin_turn(d["id"], "msg-1")
    propose_edit.func(block_id=a["id"], content="Paragraph 0, tightened.", config=cfg)
    propose_edit.func(block_id=b["id"], content="Paragraph 1, tightened.", config=cfg)
    propose_insert.func(block_type="heading", content="Risks", after_block_id=b["id"], config=cfg)
    propose_delete.func(block_id=c["id"], config=cfg)
    propose_move.func(block_id=f["id"], after_block_id=None, config=cfg)
    clock()
    got = store.accept_all(d["id"], by="Dana")
    assert got["count"] == 5 and got["checkpoint"].startswith("chk_")
    # The content had not changed since the turn began, so it is the same checkpoint, renamed.
    [cp] = store.list_checkpoints(d["id"])
    assert cp["id"] == got["checkpoint"] and cp["reason"] == "accept-all" and cp["by"] == "Dana"
    assert cp["name"] == "Before Accept all (5 suggestions)"
    assert cp["changes"] == {"edited": 2, "added": 1, "deleted": 1, "moved": 1, "total": 5}
    assert _state(store, d["id"]) != before
    clock()
    undone = store.restore_checkpoint(d["id"], got["checkpoint"], by="Dana")
    assert undone["changed"] and _state(store, d["id"]) == before
    assert store.list_suggestions(d["id"]) == []           # the suggestions stay decided ...
    assert store.get_block(a["id"])["n_versions"] == 2     # ... and their text is one step forward
    # Undoing the undo brings the accepted text back.
    store.restore_checkpoint(d["id"], undone["undo"])
    assert store.get_block(a["id"])["content"]["markdown"] == "Paragraph 0, tightened."


def test_accept_all_after_accepting_some_by_hand_checkpoints_the_content_in_between(store, cfg_for, clock):
    d, (a, b, c, *_ ) = _doc(store)
    cfg = cfg_for(d["id"])
    begin_turn(d["id"], "msg-1")
    for blk in (a, b, c):
        propose_edit.func(block_id=blk["id"], content=f"{blk['id']} tightened.", config=cfg)
    first = store.list_suggestions(d["id"])[0]
    store.accept_suggestion(first["id"])
    clock()
    got = store.accept_all(d["id"])
    assert got["count"] == 2
    acc, turn = store.list_checkpoints(d["id"])
    assert (acc["reason"], turn["reason"]) == ("accept-all", "turn") and acc["id"] == got["checkpoint"]
    assert acc["changes"]["edited"] == 2 and turn["changes"]["edited"] == 3
    store.restore_checkpoint(d["id"], acc["id"])
    assert store.get_block(a["id"])["content"]["markdown"].endswith("tightened.")   # accepted by hand: kept
    assert store.get_block(b["id"])["content"]["markdown"] == "Paragraph 1 as first written."


def test_accept_all_with_nothing_to_accept_takes_no_checkpoint(store):
    d, _ = _doc(store)
    assert store.accept_all(d["id"])["checkpoint"] is None and store.list_checkpoints(d["id"]) == []


def test_pending_suggestions_survive_a_restore_and_stale_ones_are_marked(store, cfg_for, clock):
    d, (a, b, c, *_ ) = _doc(store)
    cfg = cfg_for(d["id"])
    cp = store.create_checkpoint(d["id"], "Start")
    store.edit_block(a["id"], content={"markdown": "Edited after the checkpoint."}, author="Dana")
    new = store.insert_block(d["id"], "text", {"markdown": "Added after the checkpoint."}, author="Dana")
    begin_turn(d["id"], "msg-1")
    propose_edit.func(block_id=a["id"], content="A suggestion written against the edited text.", config=cfg)
    propose_edit.func(block_id=b["id"], content="A suggestion on a block that did not change.", config=cfg)
    propose_edit.func(block_id=new["id"], content="A suggestion on the added block.", config=cfg)
    got = store.restore_checkpoint(d["id"], cp["id"])
    assert got["notes"] == [
        "1 pending suggestion(s) were written against text that is no longer showing: they are marked outdated",
        "1 pending suggestion(s) on blocks this removed were closed"]
    by_block = {s["block_id"]: s for s in store.list_suggestions(d["id"])}
    assert by_block[a["id"]]["outdated"] is True          # written against text that is no longer showing
    assert by_block[b["id"]]["outdated"] is False         # still applies cleanly
    assert new["id"] not in by_block                      # its block is gone: obsolete, not pending
    [gone] = [s for s in store.list_suggestions(d["id"], status="obsolete")]
    assert gone["block_id"] == new["id"]
    with pytest.raises(Invalid, match="outdated"):
        store.accept_suggestion(by_block[a["id"]]["id"])
    assert store.accept_suggestion(by_block[b["id"]]["id"])["status"] == "accepted"


def test_a_block_someone_is_editing_is_left_alone_and_a_late_save_branches(store, clock):
    d, (a, b, *_ ) = _doc(store)
    cp = store.create_checkpoint(d["id"], "Start")
    mine = store.edit_block(a["id"], content={"markdown": "A, edited since."}, author="Dana")["head"]
    store.edit_block(b["id"], content={"markdown": "B, edited since."}, author="Dana")
    got = store.restore_checkpoint(d["id"], cp["id"], skip={a["id"]: "Sam is editing it"})
    assert got["skipped"] == [{"block_id": a["id"], "reason": "Sam is editing it"}] and got["edited"] == 1
    assert store.get_block(a["id"])["head"] == mine
    assert store.get_block(b["id"])["content"]["markdown"] == "Paragraph 1 as first written."
    # Without the soft lock: a tab that started typing before the restore saves on the version
    # it started from. Its text lands as a branch; the restored version stays in the tree.
    store.restore_checkpoint(d["id"], cp["id"])
    late = store.edit_block(a["id"], content={"markdown": "Typed in another tab."}, author="Sam", base=mine)
    assert late["conflict"] is True and late["content"]["markdown"] == "Typed in another tab."
    assert {a["head"], mine, late["head"]} <= {v["id"] for v in store.versions(a["id"])["versions"]}


def test_automatic_checkpoints_are_bounded_and_named_ones_are_kept(store, clock, monkeypatch):
    monkeypatch.setattr(ck, "AUTO_CAP", 3)
    d, (a, *_ ) = _doc(store)
    named = store.create_checkpoint(d["id"], "Keep me")
    for i in range(6):
        clock()
        store.edit_block(a["id"], content={"markdown": f"Edit {i}."}, author="Dana")
        store.auto_checkpoint(d["id"], "turn", group=f"turn:{i}")
    rows = store.list_checkpoints(d["id"])
    assert [c["kind"] for c in rows] == ["auto", "auto", "auto", "named"] and rows[-1]["id"] == named["id"]
    # Naming an automatic checkpoint keeps it.
    kept = store.rename_checkpoint(d["id"], rows[-2]["id"], "Before the rewrite")
    assert kept["kind"] == "named" and kept["name"] == "Before the rewrite"
    for i in range(6, 10):
        clock()
        store.edit_block(a["id"], content={"markdown": f"Edit {i}."}, author="Dana")
        store.auto_checkpoint(d["id"], "turn", group=f"turn:{i}")
    assert {named["id"], kept["id"]} <= {c["id"] for c in store.list_checkpoints(d["id"])}
    store.delete_checkpoint(d["id"], kept["id"])
    assert kept["id"] not in {c["id"] for c in store.list_checkpoints(d["id"])}
    assert store.get_block(a["id"])["n_versions"] == 11    # deleting a checkpoint deletes no content
    with pytest.raises(NotFound):
        store.delete_checkpoint(d["id"], kept["id"])
    with pytest.raises(Invalid, match="reason must be"):
        store.auto_checkpoint(d["id"], "whenever")


def test_a_checkpoint_belongs_to_its_document(store):
    d1, _ = _doc(store)
    d2, _ = _doc(store)
    cp = store.create_checkpoint(d1["id"], "Mine")
    for call in (store.checkpoint_changes, store.restore_checkpoint, store.delete_checkpoint, store.get_checkpoint):
        with pytest.raises(NotFound):
            call(d2["id"], cp["id"])
    with pytest.raises(NotFound):
        store.rename_checkpoint(d2["id"], cp["id"], "Theirs")


def test_a_data_directory_from_before_checkpoints_opens_with_none(tmp_path):
    """A 0.10.0 database has no checkpoints table: it is created on open, and old documents
    simply have no checkpoints until one is taken."""
    s = Store(tmp_path / "codraft.db")
    d = s.create_document("Old")
    a = s.insert_block(d["id"], "text", {"markdown": "Written before 0.11."}, author="Dana")
    s.close()
    db = sqlite3.connect(tmp_path / "codraft.db")
    db.execute("DROP TABLE checkpoints")
    db.commit()
    db.close()
    s = Store(tmp_path / "codraft.db")
    try:
        assert s.list_checkpoints(d["id"]) == []
        cp = s.create_checkpoint(d["id"], "First")
        s.edit_block(a["id"], content={"markdown": "Edited on 0.11."}, author="Dana")
        assert s.checkpoint_changes(d["id"], cp["id"])["counts"]["edited"] == 1
    finally:
        s.close()


def test_a_block_that_no_longer_exists_at_all_is_reported_not_raised(store):
    d, (a, b, *_ ) = _doc(store)
    cp = store.create_checkpoint(d["id"], "Start")
    store._db.execute("DELETE FROM blocks WHERE id = ?", (a["id"],))
    store._db.commit()
    store.edit_block(b["id"], content={"markdown": "Edited."}, author="Dana")
    ch = store.checkpoint_changes(d["id"], cp["id"])
    assert ch["lost"] == [a["id"]] and ch["counts"]["edited"] == 1
    got = store.restore_checkpoint(d["id"], cp["id"])
    assert got["edited"] == 1 and got["notes"] == ["1 block(s) of the checkpoint no longer exist"]


# ------------------------------------------------------------------------ agent
def test_the_agent_lists_checkpoints_and_reads_what_changed(store, cfg_for, clock):
    d, (a, b, c, e, f) = _doc(store)
    cfg = cfg_for(d["id"])
    assert "no checkpoints yet" in list_checkpoints.func(config=cfg)
    assert "no checkpoints yet" in changes_since.func(checkpoint="Sent to legal", config=cfg)
    cp = store.create_checkpoint(d["id"], "Sent to legal", by="Dana")
    clock(7200)
    out = changes_since.func(checkpoint=cp["id"], config=cfg)
    assert f'Changes since checkpoint {cp["id"]} "Sent to legal" (2 h ago): nothing.' in out
    store.edit_block(a["id"], content={"markdown": "Paragraph 0 as later rewritten, with a new clause."}, author="Dana")
    new = store.insert_block(d["id"], "heading", {"text": "Risks"}, after=b["id"], author="Dana")
    store.delete_block(c["id"])
    store.move_block(f["id"], None)
    listed = list_checkpoints.func(config=cfg)
    assert cp["id"] in listed and '"Sent to legal"' in listed and "2 h ago" in listed
    assert "since then: 1 edited, 1 added, 1 deleted, 1 moved" in listed
    for ref in (cp["id"], "sent to legal", "legal"):       # by id, by name in any case, by part of the name
        out = changes_since.func(checkpoint=ref, config=cfg)
        assert "1 edited, 1 added, 1 deleted, 1 moved. The document was 25 words then and is 25 now." in out
    assert f"- [{a['id']}] text, 5 -> 9 words:" in out
    assert "[-first written.-]" in out and "{+later rewritten, with a new clause.+}" in out
    assert f"- [{new['id']}] heading, 1 words — Risks" in out
    assert f"- [{c['id']}] text, 5 words — Paragraph 2 as first written." in out
    assert f"- [{f['id']}] text, moved up: now the first block" in out
    assert "Only the author can go back to a checkpoint" in out
    assert "No checkpoint 'nope'" in changes_since.func(checkpoint="nope", config=cfg)


def test_the_agent_restoring_a_deleted_block_is_checkpointed_too(store, cfg_for):
    d, (a, *_ ) = _doc(store)
    store.delete_block(a["id"])
    begin_turn(d["id"], "msg-1")
    assert "Restored text block" in restore_block.func(block_id=a["id"], config=cfg_for(d["id"]))
    [cp] = store.list_checkpoints(d["id"])
    assert cp["reason"] == "turn" and cp["changes"] == {"edited": 0, "added": 1, "deleted": 0, "moved": 0, "total": 1}


def test_the_tools_are_read_only_in_the_set_and_in_the_prompt():
    names = [t.name for t in CODRAFT_TOOLS]
    assert "list_checkpoints" in names and "changes_since" in names and len(names) == 31
    # The agent can read checkpoints; going back to one is the author's alone.
    assert not [n for n in names if "checkpoint" in n and n != "list_checkpoints"]
    assert "you cannot\n    restore one" in changes_since.description or "cannot" in changes_since.description
    assert "`list_checkpoints`, then\n  `changes_since`" in CODRAFT_PROMPT
    assert "you\n  have no tool for it" in CODRAFT_PROMPT


async def test_an_mcp_client_reads_checkpoints_and_its_proposals_are_checkpointed(store):
    d, (a, *_ ) = _doc(store)
    server = build_mcp_server()
    tools = {t.name for t in await server.list_tools()}
    assert {"list_checkpoints", "changes_since"} <= tools and not {t for t in tools if "restore_checkpoint" in t}

    async def call(name, **args):
        res = await server.call_tool(name, {"doc_id": d["id"], **args})
        content = res[0] if isinstance(res, tuple) else getattr(res, "content", res)
        return "\n".join(getattr(c, "text", "") for c in content)

    tools_mod._TURNS.pop(d["id"], None)
    store.create_checkpoint(d["id"], "Sent to legal")
    await call("propose_edit", block_id=a["id"], content="Proposed over MCP.")
    assert [c["reason"] for c in store.list_checkpoints(d["id"])] == ["turn", ""]
    store.accept_all(d["id"])
    out = await call("changes_since", checkpoint="Sent to legal")
    assert "1 edited" in out and "{+Proposed over MCP.+}" in out
    assert "Sent to legal" in await call("list_checkpoints")


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


def _rest_doc(client, n=3):
    did = client.post("/api/docs", json={"title": "Report"}).json()["id"]
    blocks = [client.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": f"Paragraph {i}."},
                                                            "author": "Dana"}).json() for i in range(n)]
    return did, blocks


def test_rest_names_lists_diffs_restores_renames_and_deletes(client):
    did, (a, b, c) = _rest_doc(client)
    assert client.get(f"/api/docs/{did}/checkpoints").json() == {"checkpoints": []}
    cp = client.post(f"/api/docs/{did}/checkpoints", json={"name": "Sent to legal", "author": "Dana"}).json()
    assert cp["name"] == "Sent to legal" and cp["kind"] == "named" and cp["by"] == "Dana"
    client.patch(f"/api/blocks/{a['id']}", json={"content": {"markdown": "Paragraph 0, rewritten."}})
    client.delete(f"/api/blocks/{b['id']}")
    [row] = client.get(f"/api/docs/{did}/checkpoints").json()["checkpoints"]
    assert row["id"] == cp["id"] and row["changes"] == {"edited": 1, "added": 0, "deleted": 1, "moved": 0, "total": 2}
    ch = client.get(f"/api/docs/{did}/checkpoints/{cp['id']}/changes").json()
    assert ch["edited"][0]["before"]["content"]["markdown"] == "Paragraph 0."
    assert ch["deleted"][0]["block_id"] == b["id"] and ch["checkpoint"]["name"] == "Sent to legal"
    # One block first, then everything.
    r = client.post(f"/api/docs/{did}/checkpoints/{cp['id']}/restore", json={"blocks": [a["id"]]}).json()
    assert r["result"]["edited"] == 1 and r["result"]["restored"] == 0
    assert [x["content"]["markdown"] for x in r["doc"]["blocks"]] == ["Paragraph 0.", "Paragraph 2."]
    r = client.post(f"/api/docs/{did}/checkpoints/{cp['id']}/restore", json={"author": "Dana"}).json()
    assert r["result"]["restored"] == 1 and [x["id"] for x in r["doc"]["blocks"]] == [a["id"], b["id"], c["id"]]
    undo = r["result"]["undo"]
    rows = client.get(f"/api/docs/{did}/checkpoints").json()["checkpoints"]
    assert rows[0]["id"] == undo and rows[0]["reason"] == "restore" and rows[0]["by"] == "Dana"
    # Undo the restore: the block is deleted again.
    r = client.post(f"/api/docs/{did}/checkpoints/{undo}/restore", json={}).json()
    assert [x["id"] for x in r["doc"]["blocks"]] == [a["id"], c["id"]]
    renamed = client.patch(f"/api/docs/{did}/checkpoints/{undo}", json={"name": "Without paragraph 1"}).json()
    assert renamed["kind"] == "named" and renamed["name"] == "Without paragraph 1"
    assert client.delete(f"/api/docs/{did}/checkpoints/{cp['id']}").json() == {"ok": True}
    assert cp["id"] not in {x["id"] for x in client.get(f"/api/docs/{did}/checkpoints").json()["checkpoints"]}


def test_rest_refuses_what_it_should(client):
    did, (a, *_ ) = _rest_doc(client)
    other, _ = _rest_doc(client)
    assert client.post(f"/api/docs/{did}/checkpoints", json={"name": "  "}).status_code == 400
    assert client.post("/api/docs/doc_nope/checkpoints", json={"name": "x"}).status_code == 404
    cp = client.post(f"/api/docs/{did}/checkpoints", json={"name": "Start"}).json()
    assert client.get(f"/api/docs/{did}/checkpoints/chk_nope/changes").status_code == 404
    assert client.get(f"/api/docs/{other}/checkpoints/{cp['id']}/changes").status_code == 404
    assert client.post(f"/api/docs/{other}/checkpoints/{cp['id']}/restore", json={}).status_code == 404
    r = client.post(f"/api/docs/{did}/checkpoints/{cp['id']}/restore", json={"blocks": [a["id"]]})
    assert r.status_code == 400 and "nothing to revert" in r.json()["detail"]
    assert client.patch(f"/api/docs/{did}/checkpoints/{cp['id']}", json={"name": ""}).status_code == 400
    assert client.delete(f"/api/docs/{did}/checkpoints/chk_nope").status_code == 404


def test_rest_accept_all_returns_the_checkpoint_that_undoes_it(client):
    did, (a, b, c) = _rest_doc(client)
    store = client.store
    for blk in (a, b):
        store.propose(did, "edit", {"content": {"markdown": f"{blk['id']} tightened."}}, block_id=blk["id"])
    store.propose(did, "delete", {}, block_id=c["id"])
    got = client.post(f"/api/docs/{did}/suggestions/accept-all", json={"author": "Dana"}).json()
    assert got["count"] == 3 and got["checkpoint"]
    [cp] = client.get(f"/api/docs/{did}/checkpoints").json()["checkpoints"]
    assert cp["id"] == got["checkpoint"] and cp["name"] == "Before Accept all (3 suggestions)" and cp["by"] == "Dana"
    r = client.post(f"/api/docs/{did}/checkpoints/{got['checkpoint']}/restore", json={}).json()
    assert [x["content"]["markdown"] for x in r["doc"]["blocks"]] == ["Paragraph 0.", "Paragraph 1.", "Paragraph 2."]
    # No body at all still works (older clients), and reject-all is unchanged.
    assert client.post(f"/api/docs/{did}/suggestions/accept-all").json()["checkpoint"] is None
    assert client.post(f"/api/docs/{did}/suggestions/reject-all").json()["count"] == 0


def test_rest_restore_leaves_a_block_another_tab_is_editing(client):
    did, (a, b, c) = _rest_doc(client)
    cp = client.post(f"/api/docs/{did}/checkpoints", json={"name": "Start"}).json()
    client.patch(f"/api/blocks/{a['id']}", json={"content": {"markdown": "A, edited."}})
    client.patch(f"/api/blocks/{b['id']}", json={"content": {"markdown": "B, edited."}})
    client.post(f"/api/docs/{did}/presence", json={"client_id": "tab-2", "name": "Sam", "block_id": a["id"], "editing": True})
    client.post(f"/api/docs/{did}/presence", json={"client_id": "tab-1", "name": "Dana", "block_id": b["id"], "editing": True})
    r = client.post(f"/api/docs/{did}/checkpoints/{cp['id']}/restore", json={"client_id": "tab-1"}).json()
    assert r["result"]["skipped"] == [{"block_id": a["id"], "reason": "Sam is editing it"}]
    assert r["result"]["edited"] == 1                      # my own block is not locked against me
    assert [x["content"]["markdown"] for x in r["doc"]["blocks"]] == ["A, edited.", "Paragraph 1.", "Paragraph 2."]
    client.post(f"/api/docs/{did}/presence", json={"client_id": "tab-2", "leave": True})
    r = client.post(f"/api/docs/{did}/checkpoints/{cp['id']}/restore", json={"client_id": "tab-1", "blocks": [a["id"]]}).json()
    assert r["result"]["edited"] == 1 and r["doc"]["blocks"][0]["content"]["markdown"] == "Paragraph 0."
