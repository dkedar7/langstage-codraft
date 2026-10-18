"""Codraft over MCP: let any agent harness (Claude Code, Cursor, Claude Desktop, ...)
co-author a Codraft document.

The MCP tools are the same ``CODRAFT_TOOLS`` the LangGraph agent uses. They are
wrapped with an explicit ``doc_id`` argument, because an MCP client has no
document-scoped thread, and they go through the same store. Content edits are
still *suggestions* waiting for the author's approval in the editor, and the author
watches them arrive live.

Two ways to reach it:

* **HTTP** (streamable): mounted at ``/mcp`` on a running ``langstage-codraft run``
  server. This is the best option, because the renderer is in-process, so
  ``view_pages`` works.
* **stdio**: ``langstage-codraft mcp``. It works on the same data directory. For
  ``view_pages`` it uses the renderer of a server running on the configured port,
  if there is one.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .middleware import CODRAFT_PROMPT
from .store import get_store
from .tools import CODRAFT_TOOLS

MCP_INSTRUCTIONS = (
    "Codraft documents are block-based, paged documents an author owns. Start with "
    "list_documents (or create_document), then get_document(doc_id). Every tool takes the "
    "doc_id. Content changes are suggestions the author approves in the Codraft editor.\n\n"
    + CODRAFT_PROMPT
)

# Tools whose LangChain signature we expose 1:1, plus a leading doc_id.
_SKIP = {"view_pages", "view_sketch", "view_upload"}


def _cfg(doc_id: str) -> dict:
    return {"configurable": {"thread_id": doc_id, "codraft_doc_id": doc_id}}


def build_mcp_server() -> Any:
    from mcp.server.mcpserver import Image, MCPServer

    from . import __version__
    from . import templates as tpl

    server = MCPServer(name="codraft", title="Codraft", instructions=MCP_INSTRUCTIONS,
                       version=__version__, website_url="https://github.com/dkedar7/langstage-codraft")

    @server.tool(description="List Codraft documents (id, title, block count, last update).")
    def list_documents() -> str:
        docs = get_store().list_documents()
        if not docs:
            return "No documents yet. Use create_document."
        return "\n".join(f"{d['id']}  {d['title']}  ({d['n_blocks']} blocks)" for d in docs)

    @server.tool(description=(
        "Create a Codraft document. template is one of: "
        + ", ".join(tpl.all_templates()) + ". Returns the new doc_id."))
    def create_document(title: str, template: str = "blank", columns: int | None = None) -> str:
        t = tpl.all_templates().get(template)
        if t is None:
            return f"ERROR: unknown template {template!r}"
        settings = dict(t.get("settings", {}))
        if columns:
            settings["columns"] = int(columns)
        doc = get_store().create_document(title, settings, t.get("blocks", []))
        return f"Created {doc['id']} ({title!r}, template {template})."

    for lc_tool in CODRAFT_TOOLS:
        if lc_tool.name in _SKIP:
            continue
        _register(server, lc_tool)

    @server.tool(description=(
        "See the document as the author sees it: screenshots of rendered pages "
        "(default first 4; pass page numbers). Needs a running Codraft server."))
    def view_pages(doc_id: str, pages: list[int] | None = None) -> list:
        from .render import get_renderer

        r = get_renderer()
        if r is None:
            return ["view_pages needs a running `langstage-codraft run` server (connect to its /mcp endpoint)."]
        shots = r.call(r.screenshot_pages(doc_id, pages=pages, max_pages=4))
        if not shots:
            return ["The document has no pages yet."]
        pages_seen = ", ".join(f"page {s['page']}" for s in shots)
        out: list = [f"Rendered {pages_seen} of {shots[0]['total']}."]
        out += [Image(data=Path(s["path"]).read_bytes(), format="jpeg") for s in shots]
        return out

    @server.tool(description="Look at an uploaded file: images come back as images (e.g. reference pages to match).")
    def view_upload(doc_id: str, upload_id: str) -> list:
        u = get_store().get_upload(upload_id, with_text=True)
        if u["doc_id"] != doc_id:
            return ["ERROR: that upload belongs to another document"]
        from .stylematch import summary

        look = [f"Its look: {summary(u['style'])}"] if u.get("style") else []
        if u["mime"].startswith("image/") and not u["mime"].endswith("svg+xml"):
            fmt = "png" if u["mime"].endswith("png") else "jpeg"
            return [f"{u['filename']}:", Image(data=Path(u["path"]).read_bytes(), format=fmt), *look]
        pages = [p for p in u.get("pages", []) if Path(p).exists()][:4]
        shots = [Image(data=Path(p).read_bytes(), format="jpeg") for p in pages]
        return [f"{u['filename']}:", *shots, *look, u["text"][:3000] or "(no extractable text)"]

    @server.tool(description="Look at the author's sketch on an image block (returns the drawing).")
    def view_sketch(doc_id: str, block_id: str) -> list:
        from .tools import _sketch_path

        try:
            p = _sketch_path(doc_id, block_id)
        except Exception as exc:  # noqa: BLE001
            return [f"ERROR: {exc}"]
        if p is None:
            return [f"Block {block_id} has no sketch."]
        return [f"Sketch on {block_id}:", Image(data=Path(p).read_bytes(), format="png")]

    return server


def _register(server: Any, lc_tool: Any) -> None:
    """Expose a CODRAFT_TOOLS tool over MCP with an explicit, leading doc_id parameter."""
    import inspect

    kw = inspect.Parameter.KEYWORD_ONLY
    model = lc_tool.tool_call_schema
    params = [inspect.Parameter("doc_id", kw, annotation=str)]
    annotations: dict[str, Any] = {"doc_id": str, "return": str}
    for name, f in model.model_fields.items():
        default = inspect.Parameter.empty if f.is_required() else f.default
        params.append(inspect.Parameter(name, kw, default=default, annotation=f.annotation))
        annotations[name] = f.annotation

    def call(**kwargs: Any) -> str:
        doc_id = kwargs.pop("doc_id")
        return lc_tool.invoke(kwargs, config=_cfg(doc_id))

    call.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    call.__annotations__ = annotations
    call.__name__ = lc_tool.name
    call.__doc__ = lc_tool.description
    server.tool(name=lc_tool.name,
                description=lc_tool.description + "\n\ndoc_id: the document to work on.")(call)


def image_b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def describe_tools() -> str:
    names = ["list_documents", "create_document"] + [t.name for t in CODRAFT_TOOLS if t.name not in _SKIP] + [
        "view_pages", "view_sketch"]
    return json.dumps(names)
