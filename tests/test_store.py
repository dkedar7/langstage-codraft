from pathlib import Path

import pytest

from langstage_codraft.store import Invalid, NotFound


def test_create_and_list(store):
    d = store.create_document("A", {"columns": 2})
    assert d["settings"]["columns"] == 2
    assert d["settings"]["brief"]["language"] == "English"
    assert [x["id"] for x in store.list_documents()] == [d["id"]]


def test_settings_validation(store):
    with pytest.raises(Invalid):
        store.create_document("bad", {"columns": 4})
    d = store.create_document("ok")
    with pytest.raises(Invalid):
        store.update_document(d["id"], settings={"mode": "scroll"})


def test_block_order_insert_move(store):
    d = store.create_document("A")
    a = store.insert_block(d["id"], "text", {"markdown": "a"})
    c = store.insert_block(d["id"], "text", {"markdown": "c"})
    b = store.insert_block(d["id"], "text", {"markdown": "b"}, after=a["id"])
    first = store.insert_block(d["id"], "heading", {"text": "T"}, after=None)
    ids = [x["id"] for x in store.list_blocks(d["id"])]
    assert ids == [first["id"], a["id"], b["id"], c["id"]]
    store.move_block(first["id"], c["id"])
    assert [x["id"] for x in store.list_blocks(d["id"])] == [a["id"], b["id"], c["id"], first["id"]]
    store.move_block(c["id"], None)
    assert store.list_blocks(d["id"])[0]["id"] == c["id"]


def test_reading_order_cover_flow_back(store):
    d = store.create_document("A")
    body = store.insert_block(d["id"], "text", {"markdown": "body"})
    back = store.insert_block(d["id"], "text", {"markdown": "back"}, layout={"role": "back"}, after=None)
    cover = store.insert_block(d["id"], "heading", {"text": "Cover"}, layout={"role": "cover"})
    assert [b["id"] for b in store.list_blocks(d["id"])] == [cover["id"], body["id"], back["id"]]


def test_version_tree_branching(store):
    d = store.create_document("A")
    b = store.insert_block(d["id"], "text", {"markdown": "v1"})
    v1 = b["head"]
    b = store.edit_block(b["id"], content={"markdown": "v2"})
    v2 = b["head"]
    b = store.step(b["id"], "back")
    assert b["head"] == v1 and b["content"]["markdown"] == "v1"
    b = store.step(b["id"], "forward")
    assert b["head"] == v2
    store.step(b["id"], "back")
    b = store.edit_block(b["id"], content={"markdown": "v2-alt"})  # branches from v1
    tree = store.versions(b["id"])
    by = {v["id"]: v for v in tree["versions"]}
    assert by[v1]["children"] == [v2, b["head"]]
    assert set(tree["branches"]) == {v2, b["head"]}
    with pytest.raises(Invalid):
        store.step(b["id"], "forward")
    store.checkout(b["id"], v1)
    assert store.step(b["id"], "forward")["head"] == b["head"]  # newest child


def test_noop_edit_creates_no_version(store):
    d = store.create_document("A")
    b = store.insert_block(d["id"], "text", {"markdown": "same"})
    assert store.edit_block(b["id"], content={"markdown": "same"})["n_versions"] == 1


def test_refs_are_versioned_and_cleaned(store):
    d = store.create_document("A")
    b = store.insert_block(d["id"], "text", {"markdown": "x"}, refs=["Plain title", {"url": "https://e.x"}, 3])
    assert b["refs"] == [{"title": "Plain title", "url": "", "note": ""},
                         {"title": "https://e.x", "url": "https://e.x", "note": ""}]
    store.edit_block(b["id"], refs=[])
    assert store.step(b["id"], "back")["refs"][0]["title"] == "Plain title"


def test_layout_validation_and_pins(store):
    d = store.create_document("A", {"columns": 2})
    b = store.insert_block(d["id"], "image", {})
    with pytest.raises(Invalid):
        store.set_layout(b["id"], {"repeat": "all"})  # must be pinned first
    b = store.set_layout(b["id"], {"pin": {"page": 2, "top": 100, "column": 5, "span": 9}, "repeat": "odd"})
    assert b["layout"]["pin"] == {"page": 2, "top": 100, "column": 1, "span": 1, "height": None}
    b = store.set_layout(b["id"], {"pin": None})
    assert b["layout"]["pin"] is None and b["layout"]["repeat"] is None


def test_comments_top_to_bottom(store):
    d = store.create_document("A")
    a = store.insert_block(d["id"], "text", {"markdown": "a"})
    b = store.insert_block(d["id"], "text", {"markdown": "b"})
    cb = store.add_comment(d["id"], "on b", block_id=b["id"])
    ca = store.add_comment(d["id"], "on a", block_id=a["id"])
    cd = store.add_comment(d["id"], "doc level")
    assert [c["id"] for c in store.list_comments(d["id"])] == [cd["id"], ca["id"], cb["id"]]
    store.reply_comment(ca["id"], "done", author="agent", resolve=True)
    assert [c["id"] for c in store.list_comments(d["id"])] == [cd["id"], cb["id"]]
    assert len(store.list_comments(d["id"], include_resolved=True)) == 3


def test_suggestion_gate(store):
    d = store.create_document("A")
    b = store.insert_block(d["id"], "text", {"markdown": "old"})
    s = store.propose(d["id"], "edit", {"content": {"markdown": "new"}}, block_id=b["id"], note="why")
    assert s["status"] == "pending"
    assert store.get_block(b["id"])["content"]["markdown"] == "old"
    s = store.accept_suggestion(s["id"])
    assert s["status"] == "accepted"
    blk = store.get_block(b["id"])
    assert blk["content"]["markdown"] == "new" and blk["author"] == "agent"
    with pytest.raises(Invalid):
        store.accept_suggestion(s["id"])


def test_suggestion_supersede_and_reject(store):
    d = store.create_document("A")
    b = store.insert_block(d["id"], "text", {"markdown": "old"})
    s1 = store.propose(d["id"], "edit", {"content": {"markdown": "n1"}}, block_id=b["id"])
    s2 = store.propose(d["id"], "edit", {"content": {"markdown": "n2"}}, block_id=b["id"])
    assert store.get_suggestion(s1["id"])["status"] == "superseded"
    store.reject_suggestion(s2["id"], "nope")
    assert store.get_block(b["id"])["content"]["markdown"] == "old"
    assert store.list_suggestions(d["id"]) == []


def test_auto_approval_applies(store):
    d = store.create_document("A", {"approval": "auto"})
    s = store.propose(d["id"], "insert", {"type": "heading", "content": {"text": "Hi"}, "after": "__end__"})
    assert s["status"] == "applied"
    blocks = store.list_blocks(d["id"])
    assert blocks[0]["content"]["text"] == "Hi"
    assert s["payload"]["result"]["block_id"] == blocks[0]["id"]


def test_insert_delete_move_suggestions(store):
    d = store.create_document("A")
    a = store.insert_block(d["id"], "text", {"markdown": "a"})
    b = store.insert_block(d["id"], "text", {"markdown": "b"})
    store.accept_suggestion(store.propose(d["id"], "move", {"after": None}, block_id=b["id"])["id"])
    assert store.list_blocks(d["id"])[0]["id"] == b["id"]
    store.accept_suggestion(store.propose(d["id"], "delete", {}, block_id=a["id"])["id"])
    assert [x["id"] for x in store.list_blocks(d["id"])] == [b["id"]]
    with pytest.raises(Invalid):
        store.propose(d["id"], "insert", {"type": "hologram"})


def test_rev_bumps(store):
    d = store.create_document("A")
    r0 = store.rev(d["id"])
    store.insert_block(d["id"], "text", {})
    assert store.rev(d["id"]) == r0 + 1


def test_uploads(store):
    d = store.create_document("A")
    u = store.add_upload(d["id"], "../evil name.txt", b"hello world", text="hello world")
    assert u["filename"] == "evil name.txt"
    assert store.uploads_dir in Path(u["path"]).parents
    assert store.get_upload(u["id"], with_text=True)["text"] == "hello world"
    store.delete_upload(u["id"])
    with pytest.raises(NotFound):
        store.get_upload(u["id"])


def test_duplicate_document(store):
    d = store.create_document("A")
    store.insert_block(d["id"], "text", {"markdown": "x"}, refs=[{"title": "t"}])
    c = store.duplicate_document(d["id"])
    assert c["title"] == "A (copy)"
    assert store.list_blocks(c["id"])[0]["refs"][0]["title"] == "t"


def test_accepting_the_last_suggestion_resolves_its_comment(store):
    d = store.create_document("A")
    b = store.insert_block(d["id"], "text", {"markdown": "x"})
    c = store.add_comment(d["id"], "fix", block_id=b["id"])
    s = store.propose(d["id"], "edit", {"content": {"markdown": "y"}}, block_id=b["id"], comment_id=c["id"])
    assert store.get_comment(c["id"])["status"] == "open"
    store.accept_suggestion(s["id"])
    assert store.get_comment(c["id"])["status"] == "resolved"


def test_concurrent_edit_becomes_a_branch(store):
    d = store.create_document("A")
    b = store.insert_block(d["id"], "text", {"markdown": "start"})
    base = b["head"]
    theirs = store.edit_block(b["id"], content={"markdown": "theirs"}, author="alice")
    mine = store.edit_block(b["id"], content={"markdown": "mine"}, author="bob", base=base)
    assert mine["conflict"] is True and mine["content"]["markdown"] == "mine"
    tree = store.versions(b["id"])
    by = {v["id"]: v for v in tree["versions"]}
    assert set(by[base]["children"]) == {theirs["head"], mine["head"]}  # both kept, siblings
    assert len(tree["branches"]) == 2


def test_edit_with_current_base_is_not_a_conflict(store):
    d = store.create_document("A")
    b = store.insert_block(d["id"], "text", {"markdown": "start"})
    out = store.edit_block(b["id"], content={"markdown": "next"}, base=b["head"])
    assert "conflict" not in out and out["n_versions"] == 2
