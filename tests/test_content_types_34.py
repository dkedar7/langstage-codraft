"""gh #34: a content field of the wrong type (a list for markdown, a number for a heading's
text) was stored, blanked the editor and hung exports. Writes now refuse it by name, and a
value stored by an older version is read back as text."""
import json

import pytest
from fastapi.testclient import TestClient

from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.server import create_app
from langstage_codraft.store import Invalid, Store, repair_content
from langstage_codraft.tools import insert_blocks, propose_edit


def call(tool, cfg, **args):
    return tool.invoke(args, config=cfg)


@pytest.mark.parametrize(("block_type", "content", "field"), [
    ("text", {"markdown": 42}, "markdown"),
    ("text", {"markdown": {"a": 1}}, "markdown"),
    ("heading", {"text": 5}, "text"),
    ("callout", {"markdown": "ok", "tone": ["info"]}, "tone"),
    ("image", {"src": 3}, "src"),
    ("image", {"caption": ["a"]}, "caption"),
    ("video", {"loop": "yes"}, "loop"),
    ("table", {"header": "no"}, "header"),
    ("table", {"rows": "a,b"}, "rows"),
    ("table", {"rows": [["a", {"b": 1}]]}, "rows"),
    ("chart", {"spec": "[1, 2]"}, "spec"),
    ("references", {"title": 1}, "title"),
    ("spacer", {"label": True}, "label"),
])
def test_insert_refuses_a_wrong_typed_field_by_name(store, block_type, content, field):
    d = store.create_document("D")
    with pytest.raises(Invalid, match=rf"content\.{field} of a {block_type} block"):
        store.insert_block(d["id"], block_type, content)
    assert store.list_blocks(d["id"]) == []


@pytest.mark.parametrize("level", [9, 0, "2", 2.0, True])
def test_heading_level_must_be_a_small_whole_number(store, level):
    d = store.create_document("D")
    with pytest.raises(Invalid, match="content.level"):
        store.insert_block(d["id"], "heading", {"text": "T", "level": level})


def test_a_list_of_paragraphs_is_joined_into_markdown(store):
    d = store.create_document("D")
    b = store.insert_block(d["id"], "callout", {"markdown": ["Note one", "Note two"]})
    assert b["content"]["markdown"] == "Note one\n\nNote two"


def test_the_shapes_that_were_already_fine_still_are(store):
    d = store.create_document("D")
    store.insert_block(d["id"], "text", {"markdown": None})
    store.insert_block(d["id"], "text", {})
    store.insert_block(d["id"], "image", {"alt": "no src"})
    store.insert_block(d["id"], "table", {"rows": [["a", 1, 2.5, None]]})
    store.insert_block(d["id"], "heading", {"text": "T", "level": 4})
    store.insert_block(d["id"], "text", {"markdown": "x", "extra": [1, 2]})  # unknown keys pass
    assert len(store.list_blocks(d["id"])) == 6


def test_edit_refuses_a_wrong_typed_field_and_keeps_the_block(store):
    d = store.create_document("D")
    b = store.insert_block(d["id"], "heading", {"text": "Title"})
    with pytest.raises(Invalid, match="content.text of a heading block"):
        store.edit_block(b["id"], content={"text": 5})
    assert store.get_block(b["id"])["n_versions"] == 1


def test_a_suggestion_is_checked_when_proposed_not_when_accepted(store):
    d = store.create_document("D")
    b = store.insert_block(d["id"], "text", {"markdown": "A"})
    with pytest.raises(Invalid, match="content.markdown of a text block"):
        store.propose(d["id"], "edit", {"content": {"markdown": 42}}, block_id=b["id"])
    with pytest.raises(Invalid, match="content.text of a heading block"):
        store.propose(d["id"], "insert", {"type": "heading", "content": {"text": 5}})
    assert store.list_suggestions(d["id"]) == []
    s = store.propose(d["id"], "insert", {"type": "callout", "content": {"markdown": ["a", "b"]}})
    assert s["payload"]["content"]["markdown"] == "a\n\nb"


def test_an_edit_suggestion_without_a_block_still_says_so(store):
    d = store.create_document("D")
    with pytest.raises(Invalid, match="needs a block_id"):
        store.propose(d["id"], "edit", {"content": {"markdown": "x"}})


def test_agent_tools_report_the_field(store, cfg_for):
    d = store.create_document("D")
    t = store.insert_block(d["id"], "text", {"markdown": "A"})
    out = call(insert_blocks, cfg_for(d["id"]), blocks=[
        {"type": "heading", "content": {"text": 5}},
        {"type": "callout", "content": {"markdown": ["Note one", "Note two"]}},
    ])
    lines = out.splitlines()
    assert lines[0].startswith("0: ERROR") and "content.text" in lines[0]
    assert "callout suggested" in lines[1]
    out = call(propose_edit, cfg_for(d["id"]), block_id=t["id"], content='{"markdown": 42}')
    assert "content.markdown" in out
    out = call(propose_edit, cfg_for(d["id"]), block_id=t["id"], content='{"markdown": ["a", "b"]}')
    assert "ERROR" not in out
    (s,) = store.list_suggestions(d["id"], status="pending")[1:]
    assert s["payload"]["content"]["markdown"] == "a\n\nb"


def _poke(store, sql, *args):
    store._db.execute(sql, args)
    store._db.commit()


def test_values_stored_by_an_older_version_read_back_as_text(store):
    d = store.create_document("D")
    t = store.insert_block(d["id"], "text", {"markdown": "A"})
    h = store.insert_block(d["id"], "heading", {"text": "H"})
    _poke(store, "UPDATE versions SET content = ? WHERE id = ?", json.dumps({"markdown": ["a", 1]}), t["head"])
    _poke(store, "UPDATE versions SET content = ? WHERE id = ?", json.dumps({"text": 5, "level": "x"}), h["head"])
    assert store.get_block(t["id"])["content"]["markdown"] == "a\n\n1"
    assert store.get_block(h["id"])["content"] == {"text": "5", "level": 1}
    assert store.versions(t["id"])["versions"][0]["content"]["markdown"] == "a\n\n1"
    s = store.propose(d["id"], "edit", {"content": {"markdown": "B"}}, block_id=t["id"])
    _poke(store, "UPDATE suggestions SET payload = ? WHERE id = ?",
          json.dumps({"content": {"markdown": {"a": 1}}}), s["id"])
    assert store.get_suggestion(s["id"])["payload"]["content"]["markdown"] == ""
    store.reject_suggestion(s["id"])  # still reachable
    assert repair_content("text", "not a dict") == {}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    store = Store(tmp_path / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []},
                     store=store)
    with TestClient(app) as c:
        yield c


def test_rest_answers_400_naming_the_field(client):
    did = client.post("/api/docs", json={"title": "D"}).json()["id"]
    r = client.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": 42}})
    assert r.status_code == 400 and "content.markdown" in r.json()["detail"]
    b = client.post(f"/api/docs/{did}/blocks", json={"type": "heading", "content": {"text": "H"}}).json()
    r = client.patch(f"/api/blocks/{b['id']}", json={"content": {"text": 5}})
    assert r.status_code == 400 and "content.text" in r.json()["detail"]


@pytest.mark.parametrize("style", ["notadict", ["a"], 3])
def test_settings_style_of_the_wrong_type_is_a_400(client, style):
    did = client.post("/api/docs", json={"title": "D"}).json()["id"]
    r = client.patch(f"/api/docs/{did}", json={"settings": {"style": style}})
    assert r.status_code == 400 and "settings.style" in r.json()["detail"]
    assert client.patch(f"/api/docs/{did}", json={"settings": {"style": None}}).status_code == 200
