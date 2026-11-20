"""Redline moves (0.12.1, follow-up to gh #65): a block moved since the checkpoint, or by
a pending suggestion, is written as Word's move marks. Its old place is ``w:moveFrom`` and
its new one ``w:moveTo``, joined by a shared range name, so Word shows "Moved" and Accept
or Reject handles both places together.

A block that was moved and edited keeps the move marks too. At its new place the words
it kept are ``w:moveTo``, the words it gained ``w:ins`` and the words it lost ``w:del``,
which is how Word writes a move edited afterwards. Tables have no move marks, so a moved table stays a deletion and an
insertion.
"""
from __future__ import annotations

from test_redline_65 import NS, body, check_well_formed, export, plain_words, q, tracked, words


def _doc(store, n: int = 5):
    did = store.create_document("Brief")["id"]
    h = store.insert_block(did, "heading", {"text": "Contract brief", "level": 1}, author="Dana")
    p = [store.insert_block(did, "text", {"markdown": f"Clause {i} says the **supplier** pays within {i + 1}0 days."},
                            author="Dana") for i in range(n)]
    return did, h, p


def _round_trip(store, did, cp, then):
    red = export(store, did, store.redline_since(did, cp["id"]))
    check_well_formed(red)
    assert words(red, "ins") == plain_words(export(store, did))
    assert words(red, "del") == then
    return red


def _names(red, kind):
    return [m.get(q("name")) for m in body(red).iter(q(f"{kind}RangeStart"))]


def test_a_moved_block_is_a_word_move(store):
    did, h, p = _doc(store)
    cp = store.create_checkpoint(did, "Sent to legal")
    then = plain_words(export(store, did))
    store.move_block(p[4]["id"], None)  # to the start
    red = _round_trip(store, did, cp, then)
    # Nothing is a plain insertion or deletion: the block is moved, both places named alike.
    assert tracked(red, "ins") == [] and tracked(red, "del") == []
    assert ["".join(tracked(red, "moveFrom")), "".join(tracked(red, "moveTo"))] == [
        "Clause 4 says the supplier pays within 50 days."] * 2
    assert _names(red, "moveFrom") == _names(red, "moveTo") == ["move1"]
    # The paragraph marks move too, and the moveTo place is first.
    b = body(red)
    assert len(b.findall(".//w:pPr/w:rPr/w:moveFrom", NS)) == 1
    assert len(b.findall(".//w:pPr/w:rPr/w:moveTo", NS)) == 1
    first = "".join(t.text or "" for t in b.find(q("p")).iter(q("t")))
    assert first.startswith("Clause 4 says")
    # The move is credited to whoever moved it, with a date, on both places.
    for m in list(b.iter(q("moveFrom"))) + list(b.iter(q("moveTo"))):
        assert m.get(q("author")) and m.get(q("date"))


def test_a_block_moved_and_edited_keeps_its_move_and_shows_the_edit(store):
    did, h, p = _doc(store)
    cp = store.create_checkpoint(did, "Sent to legal")
    then = plain_words(export(store, did))
    store.move_block(p[3]["id"], h["id"])  # right after the heading
    store.edit_block(p[3]["id"], content={"markdown": "Clause 3 says the **buyer** pays within 40 days, net."},
                     author="Lee")
    red = _round_trip(store, did, cp, then)
    moved_to, ins = "".join(tracked(red, "moveTo")), tracked(red, "ins")
    # The old place has the whole sentence as it was; the new one the kept words as moved...
    assert "".join(tracked(red, "moveFrom")) == "Clause 3 says the supplier pays within 40 days."
    assert "Clause 3 says the " in moved_to and "supplier" not in moved_to
    # ...the new words as inserted and the lost ones as deleted there too, by the editor,
    # as Word writes a move edited afterwards (rejecting carries all of them back).
    assert "buyer" in ins and any("net." in x for x in ins)
    assert "supplier" in tracked(red, "del")
    assert {m.get(q("author")) for m in body(red).iter(q("ins")) if m.getparent().tag != q("rPr")} == {"Lee"}
    assert _names(red, "moveFrom") == _names(red, "moveTo") == ["move1"]


def test_two_moves_get_two_names(store):
    did, h, p = _doc(store)
    cp = store.create_checkpoint(did, "Sent to legal")
    then = plain_words(export(store, did))
    store.move_block(p[4]["id"], None)
    store.move_block(p[0]["id"], p[2]["id"])
    red = _round_trip(store, did, cp, then)
    assert sorted(_names(red, "moveFrom")) == sorted(_names(red, "moveTo"))
    assert len(set(_names(red, "moveTo"))) == len(_names(red, "moveTo")) >= 2
    assert tracked(red, "ins") == [] and tracked(red, "del") == []


def test_a_move_next_to_a_delete(store):
    did, h, p = _doc(store)
    cp = store.create_checkpoint(did, "Sent to legal")
    then = plain_words(export(store, did))
    store.delete_block(p[1]["id"])
    store.move_block(p[2]["id"], p[4]["id"])  # its old neighbour is gone
    red = _round_trip(store, did, cp, then)
    assert any("Clause 1 says" in x for x in tracked(red, "del"))  # the delete stays a delete
    assert not any("Clause 2 says" in x for x in tracked(red, "del") + tracked(red, "ins"))
    assert any("Clause 2 says" in x for x in tracked(red, "moveFrom"))
    assert any("Clause 2 says" in x for x in tracked(red, "moveTo"))


def test_a_moved_table_stays_a_deletion_and_an_insertion(store):
    did, h, p = _doc(store, 2)
    t = store.insert_block(did, "table", {"rows": [["Item", "Cost"], ["Hosting", "$10"]], "header": True},
                           author="Dana")
    cp = store.create_checkpoint(did, "Sent to legal")
    then = plain_words(export(store, did))
    store.move_block(t["id"], None)
    red = _round_trip(store, did, cp, then)
    assert list(body(red).iter(q("moveFrom"))) == [] and list(body(red).iter(q("moveTo"))) == []
    assert "$10" in "".join(tracked(red, "del")) and "$10" in "".join(tracked(red, "ins"))


def test_moved_and_edited_text_with_a_table_in_it_falls_back_whole(store):
    """A Markdown table inside a moved, edited text block can't be a move: the block is
    deleted where it was and inserted, whole, where it is (never diffed, so Reject doesn't
    bring the old words back twice)."""
    did, h, p = _doc(store, 2)
    md = "Costs below.\n\n| Item | Cost |\n|---|---|\n| Hosting | $10 |"
    x = store.insert_block(did, "text", {"markdown": md}, author="Dana")
    cp = store.create_checkpoint(did, "Sent to legal")
    then = plain_words(export(store, did))
    store.move_block(x["id"], None)
    store.edit_block(x["id"], content={"markdown": md.replace("Costs below.", "Costs, below.")}, author="Dana")
    red = _round_trip(store, did, cp, then)
    assert list(body(red).iter(q("moveTo"))) == []


def test_a_pending_move_is_a_word_move(store):
    did, h, p = _doc(store)
    now = plain_words(export(store, did))
    store.propose(did, "move", {"after": h["id"]}, block_id=p[4]["id"], author="agent")
    red = export(store, did, store.redline_pending(did))
    check_well_formed(red)
    assert words(red, "del") == now
    assert tracked(red, "ins") == [] and tracked(red, "del") == []
    assert _names(red, "moveFrom") == _names(red, "moveTo") == ["move1"]
    assert {m.get(q("author")) for m in body(red).iter(q("moveTo"))} == {"Drafter"}
    accepted = words(red, "ins")
    first_clause = accepted.index("Clause")
    assert accepted[first_clause:first_clause + 2] == ["Clause", "4"]


def test_a_pending_move_with_a_pending_edit(store):
    did, h, p = _doc(store)
    now = plain_words(export(store, did))
    store.propose(did, "move", {"after": h["id"]}, block_id=p[4]["id"], author="agent")
    store.propose(did, "edit", {"content": {"markdown": "Clause 4 says the **buyer** pays within 50 days."}},
                  block_id=p[4]["id"], author="Lee")
    red = export(store, did, store.redline_pending(did))
    check_well_formed(red)
    assert words(red, "del") == now
    assert "buyer" in tracked(red, "ins") and "supplier" in tracked(red, "del")
    assert "supplier" in "".join(tracked(red, "moveFrom"))
    assert {m.get(q("author")) for m in body(red).iter(q("ins")) if m.getparent().tag != q("rPr")} == {"Lee"}
