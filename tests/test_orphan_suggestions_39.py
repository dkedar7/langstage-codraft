"""gh #39: a pending suggestion whose block was deleted stayed pending forever. Accept
404'd, Accept all dropped it without reporting it, and the pending count never cleared.
Deleting a block now closes its pending suggestions as ``obsolete`` and re-anchors inserts
and moves that were to follow it; anything still pending with nothing to apply to is
``gone``: refused by Accept, reported by Accept all, dismissable with Reject."""

import pytest
from fastapi.testclient import TestClient

from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.server import create_app
from langstage_codraft.store import Invalid, Store


def _doc(store):
    d = store.create_document("D")
    a = store.insert_block(d["id"], "text", {"markdown": "First."})
    b = store.insert_block(d["id"], "text", {"markdown": "Second."})
    c = store.insert_block(d["id"], "text", {"markdown": "Third."})
    return d, a, b, c


def test_deleting_a_block_closes_its_pending_suggestions(store):
    d, _, b, _ = _doc(store)
    s = store.propose(d["id"], "edit", {"content": {"markdown": "2nd."}}, block_id=b["id"])
    store.delete_block(b["id"])
    got = store.get_suggestion(s["id"])
    assert got["status"] == "obsolete"
    assert got["payload"]["obsolete_reason"] == "block deleted"
    assert store.list_suggestions(d["id"]) == []  # the pending count clears


def test_accepting_the_agents_delete_closes_its_other_suggestions_on_that_block(store):
    """The issue's MCP case: delete + move + edit on one block, then accept the delete."""
    d, a, b, _ = _doc(store)
    dele = store.propose(d["id"], "delete", {}, block_id=b["id"])
    mv = store.propose(d["id"], "move", {"after": None}, block_id=b["id"])
    ed = store.propose(d["id"], "edit", {"content": {"markdown": "2nd."}}, block_id=b["id"])
    done = store.accept_suggestion(dele["id"])
    assert done["status"] == "accepted"
    assert store.get_suggestion(mv["id"])["status"] == "obsolete"
    assert store.get_suggestion(ed["id"])["status"] == "obsolete"
    assert store.list_suggestions(d["id"]) == []


def test_an_insert_or_move_anchored_after_a_deleted_block_follows_the_block_before_it(store):
    d, a, b, c = _doc(store)
    ins = store.propose(d["id"], "insert", {"type": "text", "content": {"markdown": "New."}, "after": b["id"]})
    mv = store.propose(d["id"], "move", {"after": b["id"]}, block_id=c["id"])
    store.delete_block(b["id"])
    assert store.get_suggestion(ins["id"])["payload"]["after"] == a["id"]
    assert store.get_suggestion(mv["id"])["payload"]["after"] == a["id"]
    store.accept_suggestion(ins["id"])
    texts = [x["content"]["markdown"] for x in store.full(d["id"])["blocks"]]
    assert texts == ["First.", "New.", "Third."]


def test_an_insert_anchored_after_a_deleted_first_block_goes_to_the_start(store):
    d, a, b, _ = _doc(store)
    ins = store.propose(d["id"], "insert", {"type": "text", "content": {"markdown": "New."}, "after": a["id"]})
    store.delete_block(a["id"])
    assert store.get_suggestion(ins["id"])["payload"]["after"] is None
    store.accept_suggestion(ins["id"])
    assert store.full(d["id"])["blocks"][0]["content"]["markdown"] == "New."


def _orphan_the_old_way(store):
    """What 0.5.15 left behind: the block deleted, its suggestion still pending."""
    d, a, b, _ = _doc(store)
    s = store.propose(d["id"], "edit", {"content": {"markdown": "2nd."}}, block_id=b["id"])
    store._db.execute("UPDATE blocks SET deleted = 1 WHERE id = ?", (b["id"],))
    store._db.commit()
    return d, b, s


def test_a_stored_orphan_is_gone_refused_by_accept_and_dismissable(store):
    d, b, s = _orphan_the_old_way(store)
    got = store.get_suggestion(s["id"])
    assert got["status"] == "pending" and got["gone"] == f"block {b['id']} was deleted"
    with pytest.raises(Invalid, match="can't be applied"):
        store.accept_suggestion(s["id"])
    with pytest.raises(Invalid, match="can't be applied"):
        store.accept_suggestion(s["id"], force=True)
    assert store.reject_suggestion(s["id"])["status"] == "rejected"
    assert store.list_suggestions(d["id"]) == []


def test_reopening_the_store_closes_orphans_left_by_older_versions(tmp_path):
    path = tmp_path / "codraft.db"
    st = Store(path)
    d, _, s = _orphan_the_old_way(st)
    st.close()
    st = Store(path)
    try:
        assert st.get_suggestion(s["id"])["status"] == "obsolete"
        assert st.list_suggestions(d["id"]) == []
    finally:
        st.close()


def test_a_stored_insert_after_a_deleted_block_is_gone(store):
    d, a, b, _ = _doc(store)
    ins = store.propose(d["id"], "insert", {"type": "text", "content": {"markdown": "New."}, "after": b["id"]})
    store._db.execute("UPDATE blocks SET deleted = 1 WHERE id = ?", (b["id"],))
    store._db.commit()
    assert "which it was to follow, was deleted" in store.get_suggestion(ins["id"])["gone"]
    with pytest.raises(Invalid, match="can't be applied"):
        store.accept_suggestion(ins["id"])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    store = Store(tmp_path / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []},
                     store=store)
    with TestClient(app) as c:
        c.store = store
        yield c


def test_rest_issue_repro_delete_then_review(client):
    """The issue's repro: the agent's edit waits, the author deletes the paragraph."""
    D = client.post("/api/docs", json={"title": "Orphans"}).json()["id"]
    blk = client.post(f"/api/docs/{D}/blocks", json={"type": "text", "content": {"markdown": "Second paragraph."}}).json()
    client.store.propose(D, "edit", {"content": {"markdown": "A more precise second paragraph."}}, block_id=blk["id"])
    assert client.delete(f"/api/blocks/{blk['id']}").status_code == 200
    assert client.get(f"/api/docs/{D}").json()["suggestions"] == []
    r = client.post(f"/api/docs/{D}/suggestions/accept-all")
    assert r.json() == {"ok": True, "count": 0, "skipped": [], "skipped_gone": []}


def test_rest_accept_all_reports_stored_orphans_and_accept_is_a_400(client):
    d, b, s = _orphan_the_old_way(client.store)
    ok = client.store.propose(d["id"], "insert", {"type": "text", "content": {"markdown": "Kept."}})
    r = client.post(f"/api/suggestions/{s['id']}/accept")
    assert r.status_code == 400 and "can't be applied" in r.json()["detail"]
    r = client.post(f"/api/docs/{d['id']}/suggestions/accept-all").json()
    assert r["count"] == 1 and r["skipped"] == [s["id"]] and r["skipped_gone"] == [s["id"]]
    assert client.store.get_suggestion(ok["id"])["status"] == "accepted"
    assert client.post(f"/api/suggestions/{s['id']}/reject").status_code == 200
    assert client.get(f"/api/docs/{d['id']}").json()["suggestions"] == []
