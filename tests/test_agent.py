import asyncio

from langstage_codraft.agent import (
    build_demo_agent,
    graph_tool_names,
    missing_tools,
    resolve_agent_object,
)
from langstage_codraft.tools import TOOL_NAMES


def _run(agent, message, thread_id):
    from langstage_core.agui import iter_event_frames

    async def go():
        return [f async for f in iter_event_frames(agent, message, thread_id)]

    return asyncio.run(go())


def test_demo_agent_has_all_tools():
    g = build_demo_agent()
    assert graph_tool_names(g) >= TOOL_NAMES
    assert missing_tools(g) == []


def test_demo_addresses_comments_top_to_bottom(store):
    d = store.create_document("Demo")
    a = store.insert_block(d["id"], "text", {"markdown": "First paragraph. It has two sentences."})
    b = store.insert_block(d["id"], "text", {"markdown": "Second block text."})
    store.add_comment(d["id"], "expand this", block_id=b["id"])
    store.add_comment(d["id"], "make it shorter", block_id=a["id"])
    frames = _run(build_demo_agent(), "Please address all comments", d["id"])
    types = [f["type"] for f in frames]
    assert "error" not in types, frames
    assert types[-1] == "complete"
    edits = [f for f in frames if f["type"] == "tool_start" and f["name"] == "propose_edit"]
    assert [e["args"]["block_id"] for e in edits] == [a["id"], b["id"]]  # top to bottom
    sugg = store.list_suggestions(d["id"])
    assert len(sugg) == 2
    # Tools run concurrently, so creation order can interleave; match by block.
    by_block = {s["block_id"]: s for s in sugg}
    assert by_block[a["id"]]["payload"]["content"]["markdown"] == "First paragraph."
    assert "more detail" in by_block[b["id"]]["payload"]["content"]["markdown"]
    assert all(c["replies"] for c in store.list_comments(d["id"]))
    text = "".join(f.get("content", "") for f in frames if f["type"] == "content")
    assert "proposed 2 edit" in text


def test_demo_draft_insert(store):
    d = store.create_document("Demo")
    frames = _run(build_demo_agent(), "write a paragraph about solar panels", d["id"])
    assert frames[-1]["type"] == "complete"
    s = store.list_suggestions(d["id"])
    assert s and s[0]["kind"] == "insert" and "solar panels" in s[0]["payload"]["content"]["markdown"]


def test_factory_receives_tools_and_middleware():
    seen = {}

    def make(tools, middleware):
        from langchain.agents import create_agent

        seen["tools"] = tools
        seen["mw"] = middleware
        return create_agent(_fake(), tools=tools, middleware=middleware)

    g, kind = resolve_agent_object(make)
    assert kind == "factory" and len(seen["tools"]) == len(TOOL_NAMES)
    assert missing_tools(g) == []


def test_plain_graph_without_tools_reports_missing():
    from langchain.agents import create_agent

    g, kind = resolve_agent_object(create_agent(_fake(), tools=[]))
    assert kind == "graph" and set(missing_tools(g)) == set(TOOL_NAMES)


def _fake():
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    class M(GenericFakeChatModel):
        def bind_tools(self, tools, **kw):
            return self

    return M(messages=iter([AIMessage(content="hi")]))


def test_default_agent_tools_build_with_openrouter_key(monkeypatch):
    """Regression: generate_image's RunnableConfig annotation must resolve (it broke agent build)."""
    from langstage_codraft.agent import build_default_agent, research_tools

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dummy")
    names = {t.name for t in research_tools()}
    assert {"fetch_url", "generate_image"} <= names
    gen = next(t for t in research_tools() if t.name == "generate_image")
    assert "config" not in gen.tool_call_schema.model_json_schema()["properties"]
    g = build_default_agent("openrouter:anthropic/claude-haiku-5.5")
    assert "generate_image" in graph_tool_names(g) and "view_sketch" in graph_tool_names(g)
