"""gh #37: an agent edit proposed before the author edited the block was applied over the
author's newer text (Accept all did it unseen). A suggestion now records the version it was
based on; once the block moves on it is outdated, Accept all skips it, and a single accept
needs force and lands as a branch off that base. gh #36: REST refuses an unknown template."""
import json

import pytest
from fastapi.testclient import TestClient

from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.server import create_app
from langstage_codraft.store import Invalid, Store

ORIG = "First. Second. Third. Fourth."
NEWER = ORIG + " My important new conclusion."


def _setup(store):
    d = store.create_document("D")
    b = store.insert_block(d["id"], "text", {"markdown": ORIG})
    s = store.propose(d["id"], "edit", {"content": {"markdown": "First. Second."}}, block_id=b["id"])
    return d, b, s


def test_a_fresh_suggestion_records_its_base_and_is_not_outdated(store):
    _, b, s = _setup(store)
    assert s["payload"]["base_version"] == b["head"]
    assert s["outdated"] is False
    store.accept_suggestion(s["id"])
    assert store.get_block(b["id"])["content"]["markdown"] == "First. Second."


def test_an_author_edit_after_the_proposal_marks_it_outdated(store):
    d, b, s = _setup(store)
    store.edit_block(b["id"], content={"markdown": NEWER}, base=b["head"])
    got = store.get_suggestion(s["id"])
    assert got["outdated"] is True
    assert got["base_content"]["markdown"] == ORIG  # the diff baseline is what the agent saw
    assert [x["outdated"] for x in store.full(d["id"])["suggestions"]] == [True]


def test_accepting_an_outdated_suggestion_needs_force(store):
    _, b, s = _setup(store)
    store.edit_block(b["id"], content={"markdown": NEWER})
    with pytest.raises(Invalid, match="outdated"):
        store.accept_suggestion(s["id"])
    assert store.get_block(b["id"])["content"]["markdown"] == NEWER
    assert store.get_suggestion(s["id"])["status"] == "pending"


def test_a_forced_outdated_edit_is_a_branch_off_its_base(store):
    _, b, s = _setup(store)
    author = store.edit_block(b["id"], content={"markdown": NEWER})
    done = store.accept_suggestion(s["id"], force=True)
    assert done["status"] == "accepted" and done["payload"]["result"]["branched"] is True
    blk = store.get_block(b["id"])
    assert blk["content"]["markdown"] == "First. Second."
    rows = store._q("SELECT id, parent_id, author FROM versions WHERE block_id = ?", (b["id"],))
    agent = next(r for r in rows if r["author"] == "agent")
    assert agent["parent_id"] == b["head"]  # sibling of the author's version, not its child
    assert any(r["id"] == author["head"] for r in rows)  # the author's version is kept


def test_suggestions_stored_before_base_versions_still_accept(store):
    d, b, s = _setup(store)
    payload = {k: v for k, v in s["payload"].items() if k != "base_version"}
    store._db.execute("UPDATE suggestions SET payload = ? WHERE id = ?", (json.dumps(payload), s["id"]))
    store._db.commit()
    store.edit_block(b["id"], content={"markdown": NEWER})
    assert store.get_suggestion(s["id"])["outdated"] is False
    store.accept_suggestion(s["id"])


def test_an_outdated_delete_is_also_held_back(store):
    d = store.create_document("D")
    b = store.insert_block(d["id"], "text", {"markdown": ORIG})
    s = store.propose(d["id"], "delete", {}, block_id=b["id"])
    store.edit_block(b["id"], content={"markdown": NEWER})
    with pytest.raises(Invalid, match="outdated"):
        store.accept_suggestion(s["id"])
    assert store.list_blocks(d["id"])


def test_auto_approval_still_applies_immediately(store):
    d = store.create_document("D", {"approval": "auto"})
    b = store.insert_block(d["id"], "text", {"markdown": ORIG})
    s = store.propose(d["id"], "edit", {"content": {"markdown": "Short."}}, block_id=b["id"])
    assert s["status"] == "applied" and store.get_block(b["id"])["content"]["markdown"] == "Short."


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


def test_rest_accept_all_skips_outdated_and_reports_them(client):
    """The issue's repro: the author keeps writing while the agent's suggestion waits."""
    did = client.post("/api/docs", json={"title": "Stale"}).json()["id"]
    a = client.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": ORIG}}).json()["id"]
    c = client.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Other."}}).json()["id"]
    stale = client.store.propose(did, "edit", {"content": {"markdown": "First. Second."}}, block_id=a)
    fresh = client.store.propose(did, "edit", {"content": {"markdown": "Other, edited."}}, block_id=c)
    head = client.get(f"/api/blocks/{a}/versions").json()["head"]
    client.patch(f"/api/blocks/{a}", json={"content": {"markdown": NEWER}, "base": head})

    r = client.post(f"/api/docs/{did}/suggestions/accept-all").json()
    assert r == {"ok": True, "count": 1, "skipped": [stale["id"]], "skipped_gone": []}
    blocks = {b["id"]: b["content"]["markdown"] for b in client.get(f"/api/docs/{did}").json()["blocks"]}
    assert blocks == {a: NEWER, c: "Other, edited."}

    r = client.post(f"/api/suggestions/{stale['id']}/accept")
    assert r.status_code == 400 and "outdated" in r.json()["detail"]
    r = client.post(f"/api/suggestions/{stale['id']}/accept", json={"force": True})
    assert r.status_code == 200 and r.json()["payload"]["result"]["branched"] is True
    assert fresh["id"] != stale["id"]


def test_rest_refuses_an_unknown_template(client):
    r = client.post("/api/docs", json={"title": "Q3 report", "template": "reprot"})
    assert r.status_code == 400
    assert "unknown template 'reprot'; available:" in r.json()["detail"]
    assert client.get("/api/docs").json() == [] or all(
        d["title"] != "Q3 report" for d in client.get("/api/docs").json())


@pytest.mark.parametrize("body", [{"title": "A"}, {"title": "B", "template": ""},
                                  {"title": "C", "template": None}, {"title": "D", "template": "report"}])
def test_rest_missing_or_known_template_still_works(client, body):
    assert client.post("/api/docs", json=body).status_code == 200
