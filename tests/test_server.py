import json

import pytest
from fastapi.testclient import TestClient

from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.server import create_app
from langstage_codraft.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    store = Store(tmp_path / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []},
                     store=store)
    with TestClient(app) as c:
        yield c


def test_config_and_templates(client):
    cfg = client.get("/api/config").json()
    assert cfg["chat_available"] and "classic" in cfg["themes"]
    assert {t["id"] for t in cfg["templates"]} >= {"blank", "report", "paper", "newsletter"}


def test_document_lifecycle(client):
    doc = client.post("/api/docs", json={"template": "report", "settings": {"brief": {"audience": "board"}}}).json()
    assert doc["title"] == "Report" and len(doc["blocks"]) == 11
    assert doc["settings"]["brief"]["audience"] == "board"
    assert doc["blocks"][0]["layout"]["role"] == "cover"
    did = doc["id"]
    blank = client.post("/api/docs", json={}).json()
    assert blank["title"] == "Untitled document" and blank["blocks"] == []
    r = client.patch(f"/api/docs/{did}", json={"title": "Q3", "settings": {"columns": 2}}).json()
    assert r["title"] == "Q3" and r["settings"]["columns"] == 2
    assert client.patch(f"/api/docs/{did}", json={"settings": {"theme": "nope"}}).status_code == 400
    assert client.patch(f"/api/docs/{did}", json={"settings": {"columns": 5}}).status_code == 400
    assert len(client.get("/api/docs").json()) == 2
    assert client.post(f"/api/docs/{did}/duplicate").json()["title"] == "Q3 (copy)"
    assert client.delete(f"/api/docs/{did}").json()["ok"]
    assert client.get(f"/api/docs/{did}").status_code == 404


def test_blocks_versions_comments_suggestions(client):
    did = client.post("/api/docs", json={}).json()["id"]
    b = client.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "one"}}).json()
    rev0 = client.get(f"/api/docs/{did}/rev").json()["rev"]
    b2 = client.patch(f"/api/blocks/{b['id']}", json={"content": {"markdown": "two"}}).json()
    assert b2["n_versions"] == 2
    assert client.get(f"/api/docs/{did}/rev").json()["rev"] > rev0
    back = client.post(f"/api/blocks/{b['id']}/checkout", json={"direction": "back"}).json()
    assert back["content"]["markdown"] == "one"
    assert client.post(f"/api/blocks/{b['id']}/checkout", json={"direction": "back"}).status_code == 400
    tree = client.get(f"/api/blocks/{b['id']}/versions").json()
    assert len(tree["versions"]) == 2
    lay = client.patch(f"/api/blocks/{b['id']}", json={"layout": {"span": "full"}}).json()
    assert lay["layout"]["span"] == "full"
    assert client.patch(f"/api/blocks/{b['id']}", json={"layout": {"span": "wide"}}).status_code == 400
    c = client.post(f"/api/docs/{did}/comments", json={"body": "expand", "block_id": b["id"]}).json()
    assert c["status"] == "open"
    # agent turn via the SSE chat endpoint
    with client.stream("POST", f"/api/docs/{did}/chat", json={"message": "address all comments"}) as r:
        frames = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert frames[-1]["type"] == "complete", frames
    doc = client.get(f"/api/docs/{did}").json()
    assert len(doc["suggestions"]) == 1
    sid = doc["suggestions"][0]["id"]
    acc = client.post(f"/api/suggestions/{sid}/accept", json={}).json()
    assert acc["status"] == "accepted"
    blk = client.get(f"/api/docs/{did}").json()["blocks"][0]
    assert "more detail" in blk["content"]["markdown"] and blk["n_versions"] == 3
    hist = client.get(f"/api/docs/{did}/messages").json()
    assert hist[0]["role"] == "user" and any(m.get("tool_calls") for m in hist if m["role"] == "assistant")
    # comment reply + resolve as the human
    c2 = client.patch(f"/api/comments/{c['id']}", json={"reply": "thanks", "resolve": True}).json()
    assert c2["status"] == "resolved" and c2["replies"][-1]["author"] == "user"


def test_chat_validation(client):
    did = client.post("/api/docs", json={}).json()["id"]
    assert client.post(f"/api/docs/{did}/chat", json={"message": " "}).status_code == 400
    assert client.post("/api/docs/doc_nope/chat", json={"message": "hi"}).status_code == 404


def test_uploads_extract_and_serve(client):
    did = client.post("/api/docs", json={}).json()["id"]
    r = client.post(f"/api/docs/{did}/uploads", files={"file": ("n.md", b"# Notes\nalpha beta", "text/markdown")},
                    data={"purpose": "reference"}).json()
    assert r["chars"] > 5 and r["purpose"] == "reference"
    assert client.get(r["url"]).content == b"# Notes\nalpha beta"
    bad = client.post(f"/api/docs/{did}/uploads", files={"file": ("n.md", b"x", "text/markdown")},
                      data={"purpose": "weird"})
    assert bad.status_code == 400


def test_bulk_accept(client):
    did = client.post("/api/docs", json={}).json()["id"]
    with client.stream("POST", f"/api/docs/{did}/chat", json={"message": "write about tides"}) as r:
        list(r.iter_lines())
    assert client.post(f"/api/docs/{did}/suggestions/accept-all").json()["count"] == 1
    assert len(client.get(f"/api/docs/{did}").json()["blocks"]) == 1


def test_agui_endpoint_mounted(client):
    assert any(getattr(r, "path", None) == "/agui" for r in client.app.routes)


def test_template_cover_heading_takes_title(client):
    doc = client.post("/api/docs", json={"template": "report", "title": "Q3 review"}).json()
    assert doc["blocks"][0]["content"]["text"] == "Q3 review"


def test_presence_roundtrip_and_expiry(client, monkeypatch):
    did = client.post("/api/docs", json={}).json()["id"]
    r = client.post(f"/api/docs/{did}/presence", json={"client_id": "c1", "name": "Ada", "block_id": "blk_x", "editing": True}).json()
    assert r["presence"] == [{"client_id": "c1", "name": "Ada", "color": "#2563eb", "block_id": "blk_x", "editing": True}]
    client.post(f"/api/docs/{did}/presence", json={"client_id": "c2", "name": "Bo"})
    assert len(client.post(f"/api/docs/{did}/presence", json={"client_id": "c2", "name": "Bo"}).json()["presence"]) == 2
    assert len(client.post(f"/api/docs/{did}/presence", json={"client_id": "c2", "leave": True}).json()["presence"]) == 1
    import langstage_codraft.server as srv
    monkeypatch.setattr(srv, "PRESENCE_TTL", -1)
    assert client.post(f"/api/docs/{did}/presence", json={"client_id": "c3", "leave": True}).json()["presence"] == []


def test_event_stream_reports_rev(client):
    did = client.post("/api/docs", json={}).json()["id"]
    with client.stream("GET", f"/api/docs/{did}/events?once=1") as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                snap = json.loads(line[6:])
                assert snap["rev"] >= 0 and snap["presence"] == [] and snap["running"] is False
                break
    assert client.get("/api/docs/doc_nope/events").status_code == 404


def test_block_patch_with_stale_base_branches(client):
    did = client.post("/api/docs", json={}).json()["id"]
    b = client.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "v1"}}).json()
    client.patch(f"/api/blocks/{b['id']}", json={"content": {"markdown": "other tab"}})
    r = client.patch(f"/api/blocks/{b['id']}", json={"content": {"markdown": "this tab"}, "base": b["head"]}).json()
    assert r["conflict"] is True and r["content"]["markdown"] == "this tab"


def test_agui_unknown_thread_is_404(client):
    r = client.post("/agui", json={"threadId": "doc_nope", "runId": "r1", "messages": [], "state": {},
                                   "tools": [], "context": [], "forwardedProps": {}})
    assert r.status_code == 404 and "not found" in r.json()["detail"]


def test_mcp_endpoint_mounted(client):
    assert client.get("/api/config").json()["mcp_url"] == "/mcp"
    assert any(getattr(r, "path", None) == "/mcp" for r in client.app.router.routes)
