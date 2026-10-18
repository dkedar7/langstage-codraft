"""Codraft over MCP: the same tools, with an explicit doc_id, through a real MCP client."""
import json

import pytest

from langstage_codraft.mcp_server import build_mcp_server


def _text(result) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, tuple):  # (content, structured) on some SDK versions
        content = content[0]
    return "\n".join(getattr(c, "text", "") for c in content)


@pytest.fixture
def server(store):
    return build_mcp_server()


async def test_lists_all_tools_with_doc_id(server):
    tools = {t.name: t for t in await server.list_tools()}
    assert {"list_documents", "create_document", "get_document", "propose_edit", "view_pages",
            "view_sketch", "reply_to_comment"} <= set(tools)
    schema = tools["propose_edit"].input_schema if hasattr(tools["propose_edit"], "input_schema") else tools["propose_edit"].inputSchema
    assert "doc_id" in schema["required"] and "block_id" in schema["required"]
    assert "config" not in schema["properties"]


async def test_mcp_agent_suggests_and_author_approves(server, store):
    out = _text(await server.call_tool("create_document", {"title": "Via MCP", "template": "memo"}))
    doc_id = out.split()[1]
    outline = _text(await server.call_tool("get_document", {"doc_id": doc_id}))
    assert "Via MCP" in outline
    block = next(b for b in store.list_blocks(doc_id) if b["type"] == "text")
    res = _text(await server.call_tool("propose_edit", {
        "doc_id": doc_id, "block_id": block["id"], "content": "Rewritten from Claude Code.",
        "note": "tighter"}))
    assert "waiting for the author's approval" in res
    sugg = store.list_suggestions(doc_id)
    assert sugg and sugg[0]["payload"]["content"]["markdown"] == "Rewritten from Claude Code."
    assert block["id"] in _text(await server.call_tool("list_documents", {})) or doc_id in _text(
        await server.call_tool("list_documents", {}))


async def test_view_pages_without_renderer_explains(server, store):
    doc = store.create_document("x")
    out = _text(await server.call_tool("view_pages", {"doc_id": doc["id"]}))
    assert "running" in out


async def test_view_sketch_returns_image(server, store):
    doc = store.create_document("x")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    up = store.add_upload(doc["id"], "sketch.png", png, purpose="asset", mime="image/png")
    b = store.insert_block(doc["id"], "image", {"sketch": up["id"]})
    res = await server.call_tool("view_sketch", {"doc_id": doc["id"], "block_id": b["id"]})
    content = getattr(res, "content", res)
    if isinstance(content, tuple):
        content = content[0]
    assert any(getattr(c, "type", "") == "image" for c in content), json.dumps(str(content))[:300]


async def test_stdio_command_serves_tools(tmp_path):
    """`langstage-codraft mcp` over stdio, as Claude Desktop / Cursor would launch it."""
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "langstage_codraft.cli", "mcp", "--data-dir", str(tmp_path), "--port", "1"])
    async with stdio_client(params) as streams, ClientSession(*streams[:2]) as s:
        await s.initialize()
        out = await s.call_tool("create_document", {"title": "Over stdio"})
        assert "Created doc_" in out.content[0].text
        listing = await s.call_tool("list_documents", {})
        assert "Over stdio" in listing.content[0].text
