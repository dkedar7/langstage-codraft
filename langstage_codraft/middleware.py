"""``CodraftMiddleware``: everything a LangChain agent needs to co-author a document.

Adding it to ``create_agent(..., middleware=[CodraftMiddleware()])`` does three things:

1. registers ``CODRAFT_TOOLS`` (so you don't have to list them),
2. adds the co-author system prompt, including the document's brief and approval mode,
3. attaches the page screenshots from ``view_pages`` to the model call as images.
   OpenAI-compatible APIs accept images only in user messages, not in tool results,
   so the screenshots go into a transient user message. That message is never saved
   to the thread.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from .tools import CODRAFT_TOOLS

CODRAFT_PROMPT = """\
You are the co-author inside Codraft, a block-based document editor. The author owns
the ideas and has the final say. You own the styling, and you help with drafting,
research and revision when asked.

How you work:
- Start with `get_document` to see the brief, the outline and the comments. Read only
  the blocks you need with `read_blocks`. Never paste the whole document back.
- Content changes are SUGGESTIONS (`propose_edit`, `propose_insert`, `propose_delete`,
  `propose_move`). When approval is "required", the author accepts or rejects them, so
  propose each change once and move on. Give every suggestion a one-line `note`.
- Styling is yours: `set_block_layout` and `set_document_style` apply immediately. Keep
  the document visually consistent with its theme.
- Comments: when asked to address comments, call `list_comments` and handle the open
  ones strictly top to bottom. Tie each change to its comment (`comment_id`) and reply
  with `reply_to_comment`. Resolve a comment only when no approval is pending.
  Honor preferences in resolved comments and in block history (`get_block_history`).
- Cite sources in `references` on the block you change (title, url, note). Use
  `search_document` for uploaded reference material. Use any research tools you have.
- Blocks with a fixed height state a word budget. Stay roughly within it unless the
  author asks for more.
- When the author drew a sketch on an image block, `view_sketch` shows it. Turn it into
  an image if you have an image tool (e.g. `generate_image`), otherwise say you can't.
- Use `view_pages` to check layout visually (overflow, stranded headings, unbalanced
  columns) after structural or styling changes.
- If a request needs a capability you don't have (for example generating an image or
  browsing when you have no such tool), say so plainly in a comment reply or in chat.
  Never pretend.
- Match the brief's writing style, audience, platform and language.
"""

_MAX_IMAGES = 4


def _doc_brief(state_config: Any) -> str:
    try:
        from .store import get_store

        cfg = (state_config or {}).get("configurable", {})
        doc_id = cfg.get("codraft_doc_id") or cfg.get("thread_id")
        if not doc_id:
            return ""
        doc = get_store().get_document(doc_id)
    except Exception:  # noqa: BLE001 - no store / not a document thread
        return ""
    s = doc["settings"]
    brief = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in s.get("brief", {}).items() if v)
    return (f"\nCurrent document: {doc['title']!r} ({doc_id}). {s['mode']}, {s['columns']} column(s), "
            f"theme {s['theme']}. Approval mode: {s['approval']}."
            + (f"\nBrief: {brief}." if brief else ""))


def _images_for_turn(messages: list) -> list[str]:
    """Screenshot paths from view_pages results after the last human message."""
    paths: list[str] = []
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            break
        if isinstance(m, ToolMessage):
            art = getattr(m, "artifact", None) or {}
            if isinstance(art, dict):
                paths = list(art.get("images") or []) + paths
    return paths[-_MAX_IMAGES:]


def _image_message(paths: list[str]) -> HumanMessage | None:
    blocks: list[dict] = []
    for p in paths:
        try:
            data = Path(p).read_bytes()
        except OSError:
            continue
        mime = "image/jpeg" if p.lower().endswith((".jpg", ".jpeg")) else "image/png"
        blocks.append({"type": "image_url",
                       "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}})
    if not blocks:
        return None
    return HumanMessage(content=[{"type": "text", "text": "[Images from your last tool calls]"}] + blocks)


class CodraftMiddleware(AgentMiddleware):
    """Co-author prompt, tools and page images for a ``create_agent`` agent."""

    def __init__(self, *, extra_prompt: str = "", attach_images: bool = True, tools: list | None = None):
        super().__init__()
        self.tools = list(CODRAFT_TOOLS if tools is None else tools)
        self.extra_prompt = extra_prompt
        self.attach_images = attach_images

    def _prepare(self, request: Any) -> Any:
        from langgraph.config import get_config

        try:
            cfg = get_config()
        except Exception:  # noqa: BLE001 - outside a runnable context
            cfg = {}
        prompt = CODRAFT_PROMPT + _doc_brief(cfg) + (("\n" + self.extra_prompt) if self.extra_prompt else "")
        sys_msg = request.system_message
        if sys_msg is not None and getattr(sys_msg, "content", None):
            existing = sys_msg.content if isinstance(sys_msg.content, str) else ""
            prompt = (existing + "\n\n" + prompt) if existing else prompt
        overrides: dict[str, Any] = {"system_message": SystemMessage(content=prompt)}
        if self.attach_images:
            img = _image_message(_images_for_turn(list(request.messages)))
            if img is not None:
                overrides["messages"] = list(request.messages) + [img]
        return request.override(**overrides)

    def wrap_model_call(self, request, handler):  # type: ignore[override]
        return handler(self._prepare(request))

    async def awrap_model_call(self, request, handler):  # type: ignore[override]
        return await handler(self._prepare(request))
