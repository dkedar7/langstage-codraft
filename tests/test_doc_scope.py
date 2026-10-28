"""Every id a tool or endpoint takes alongside a doc_id must belong to that document (gh #25)."""
import pytest
from fastapi.testclient import TestClient

from langstage_codraft.agent import build_demo_agent, demo_turn
from langstage_codraft.config import CodraftConfig
from langstage_codraft.mcp_server import build_mcp_server
from langstage_codraft.server import create_app
from langstage_codraft.store import Invalid, Store
from langstage_codraft.tools import (
    get_block_history,
    propose_insert,
    propose_move,
    read_blocks,
    set_block_layout,
)


def _text(result) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, tuple):  # (content, structured) on some SDK versions
        content = content[0]
    return "\n".join(getattr(c, "text", "") for c in content)


def call(tool, doc_id, **args):
    return tool.invoke(args, config={"configurable": {"thread_id": doc_id}})


@pytest.fixture
def two(store):
    a = store.create_document("Doc A")
    b = store.create_document("Doc B")
    blk_a = store.insert_block(a["id"], "text", {"markdown": "Secret from A."})
    blk_b = store.insert_block(b["id"], "text", {"markdown": "Body of B."})
    return a["id"], b["id"], blk_a, blk_b


def test_read_blocks_refuses_another_documents_block(store, two):
    a, b, blk_a, blk_b = two
    out = call(read_blocks, b, block_ids=[blk_a["id"], blk_b["id"]])
    assert f"[{blk_a['id']}] ERROR: block {blk_a['id']} is not in document {b}" in out
    assert "Secret from A." not in out
    assert "Body of B." in out  # the block that does belong is still read


def test_get_block_history_refuses_another_documents_block(store, two):
    a, b, blk_a, _ = two
    out = call(get_block_history, b, block_id=blk_a["id"])
    assert out.startswith("ERROR") and "is not in document" in out


def test_set_block_layout_refuses_and_leaves_the_block_alone(store, two):
    a, b, blk_a, _ = two
    before = store.get_block(blk_a["id"])["layout"]
    out = call(set_block_layout, b, block_id=blk_a["id"], full_width=True, height_px=400)
    assert out.startswith("ERROR") and "is not in document" in out
    assert store.get_block(blk_a["id"])["layout"] == before
    assert call(set_block_layout, a, block_id=blk_a["id"], full_width=True).startswith("Layout of")


def test_propose_insert_and_move_refuse_an_anchor_in_another_document(store, two):
    a, b, blk_a, blk_b = two
    assert "is not in document" in call(propose_insert, b, block_type="text", content="x",
                                         after_block_id=blk_a["id"])
    assert "is not in document" in call(propose_move, b, block_id=blk_b["id"], after_block_id=blk_a["id"])
    assert store.list_suggestions(b) == []


def test_a_suggestion_cannot_resolve_another_documents_comment(store, two):
    a, b, blk_a, blk_b = two
    c = store.add_comment(a, "Keep this open", block_id=blk_a["id"])
    with pytest.raises(Invalid, match="belongs to another document"):
        store.propose(b, "edit", {"content": {"markdown": "y"}}, block_id=blk_b["id"], comment_id=c["id"])
    assert store.get_comment(c["id"])["status"] == "open"


def test_comment_on_another_documents_block_is_refused(store, two):
    a, b, blk_a, _ = two
    with pytest.raises(Invalid, match="is not in document"):
        store.add_comment(b, "misfiled", block_id=blk_a["id"])


def test_http_comment_on_another_documents_block_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    store = Store(tmp_path / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []},
                     store=store)
    a = store.create_document("A")
    b = store.create_document("B")
    blk_a = store.insert_block(a["id"], "text", {"markdown": "x"})
    with TestClient(app) as c:
        r = c.post(f"/api/docs/{b['id']}/comments", json={"body": "hi", "block_id": blk_a["id"]})
        assert r.status_code == 400 and "is not in document" in r.text
        assert c.post(f"/api/docs/{a['id']}/comments", json={"body": "hi", "block_id": blk_a["id"]}).status_code == 200


async def test_mcp_block_tools_stay_in_their_document(store, two):
    a, b, blk_a, _ = two
    server = build_mcp_server()
    for name, args in [("read_blocks", {"block_ids": [blk_a["id"]]}),
                       ("get_block_history", {"block_id": blk_a["id"]}),
                       ("set_block_layout", {"block_id": blk_a["id"], "full_width": True})]:
        out = _text(await server.call_tool(name, {"doc_id": b, **args}))
        assert "is not in document" in out, (name, out)
    assert store.get_block(blk_a["id"])["layout"].get("span") != "full"


def test_demo_hint_has_no_html_like_placeholder():
    """The chat renders Markdown through DOMPurify, which drops an unknown `<topic>` tag (gh #24)."""
    from langchain_core.messages import AIMessage, ToolMessage

    msgs = [AIMessage(content="", tool_calls=[{"name": "get_document", "args": {}, "id": "t1"}]),
            ToolMessage(content="1. [blk_x] text", name="get_document", tool_call_id="t1")]
    reply = demo_turn(msgs)["content"]
    assert "write a paragraph about tidal energy" in reply
    assert "<" not in reply
