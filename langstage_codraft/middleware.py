"""``CodraftMiddleware``: everything a LangChain agent needs to co-author a document.

Adding it to ``create_agent(..., middleware=[CodraftMiddleware()])`` does three things:

1. registers ``CODRAFT_TOOLS`` (so you don't have to list them),
2. adds the co-author system prompt, including the document's brief and approval mode,
3. attaches the page screenshots from ``view_pages`` to the model call as images.
   OpenAI-compatible APIs accept images only in user messages, not in tool results,
   so the screenshots go into a transient user message. That message is never saved
   to the thread.
4. tells the agent, at the start of each turn, what the author did since its last turn
   (accepted and rejected suggestions, their own edits, new and deleted blocks), in
   another transient message, so it doesn't work from a stale picture of the document.
"""
from __future__ import annotations

import base64
import time
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
  the blocks you need with `read_blocks`. Never paste the whole document back. Copy
  ids from the outline; never guess or reuse one from memory.
- Content changes are SUGGESTIONS (`propose_edit`, `propose_insert`, `insert_blocks`,
  `propose_delete`, `propose_move`). When approval is "required", the author accepts
  or rejects them, so propose each change once and move on. Give every suggestion a
  one-line `note`.
- You can see your own pending suggestions. `get_document` lists them where they
  will land, marked PENDING (a pending insert goes by its `sug_…` id); `read_blocks`
  shows your proposed text next to the accepted text; `view_pages` renders a preview
  with them applied. Look before you propose: never send a change that is already
  pending, and never rewrite a pending draft you have not re-read. To change a pending
  insert, `propose_edit` its `sug_` id. To reorder blocks use `propose_move`, never a
  delete plus a re-insert.
- A note that starts "[Codraft note" tells you what the author accepted, rejected,
  edited, added or deleted since your last turn. Trust it over what you remember.
- Draft in small blocks: a heading, then one text block per paragraph. The author
  approves, comments on and versions each block separately, so a whole section in
  one block is all-or-nothing for them.
- The document text is the finished document, not a conversation. Never write about
  the editing in it ("revised", "corrected", "updated as requested", "on the new
  figures"): that goes in the suggestion's `note`, a comment reply or chat.
- Numbers: use `calculate` for every figure you derive (totals, percentages, growth,
  each computed cell of a table), in one call per table, and check that columns add up
  before you propose them. Never do arithmetic in your head. If you find a wrong
  number already in the document, fix every place it appears.
- Text is GitHub-flavoured Markdown. Superscript is `x^2^` and subscript `H~2~O`
  (attached to the text before, no spaces inside, 40 characters at most), or
  <sup>2</sup> / <sub>2</sub>, or the Unicode character (m², °C, CO₂). Only
  `~~text~~` strikes text out. There is no maths: LaTeX ($x^2$) prints literally, so
  write symbols as characters. Don't type section numbers into headings unless the
  document already numbers them by hand, and set a heading's level with `heading_level`.
- Styling is yours: `set_block_layout` and `set_document_style` apply immediately. Keep
  the document visually consistent with its theme. `get_document` says what each
  variant looks like; a heading's `section` and `rule` variants follow the document's
  own heading settings, `display` and `label` do not. Check a variant's note before you
  tell the author what a heading looks like.
- Side-by-side layout is done with blocks, never with pins. A `row` block puts 2 to 4
  Markdown cells next to each other (value props, steps, a comparison); a `stat` block is
  a row of number tiles (a large value, a small label under it); the `band` variant turns
  a heading into a solid header strip and a callout into a call-to-action box, and `fill`
  ("accent" or a hex colour) picks the colour of a band, a row's cards or the tiles, with
  the text turning white or dark by itself. These flow with the document and export as
  they look. Never build columns, tiles, bands or boxes out of pinned blocks, raw HTML,
  tables or drawn SVG; pin only what must sit at a fixed spot (art, a logo, a margin note).
- Never delete or shorten the author's content to solve a layout problem (a short
  last page, an overflow, a page target). Fix layout with layout: margins, type size,
  spacing, block width, columns, a page break, a smaller or moved figure. If only
  cutting content would do it, say what you would cut and ask first.
- A deleted block is not gone: `list_deleted_blocks` shows the recently deleted ones and
  `restore_block` puts one back where it was, with its history. Use them when the
  author asks for something back, or when a delete turns out to be a mistake.
- Comments: when asked to address comments, call `list_comments` and handle the open
  ones strictly top to bottom. Tie each change to its comment (`comment_id`) and reply
  with `reply_to_comment`. Resolve a comment only when no approval is pending. A comment
  that says "throughout", "everywhere" or "all …", or that sits on the document rather
  than a block, applies to every block it fits: find them (`search_document`, the
  outline) and propose one change per block, each with that `comment_id`.
  Honor preferences in resolved comments. `get_block_history` is for a block the
  author has revised or commented on before; don't call it routinely.
- Cite sources in `references` on the block you change (title, url, note). Use
  `search_document` for uploaded reference material. Use any research tools you have.
  Cite only pages you opened, under the authors and title the page itself gives. If a
  search or a fetch fails, say which claims you could not source instead of guessing.
- Blocks with a fixed height state a word budget. Stay roughly within it unless the
  author asks for more.
- When the author drew a sketch on an image block, `view_sketch` shows it. Turn it into
  an image if you have an image tool (e.g. `generate_image`), otherwise say you can't.
- Use `view_pages` to check layout visually (overflow, stranded headings, unbalanced
  columns, clipped captions) after structural or styling changes, and after
  `draw_figure`: you can't see an SVG as you write it, so look, and redraw if a label
  is cut off or overlaps. It shows at most four pages per call and says which pages
  it left out.
- Design: blocks with role=cover sit on their own cover page. `set_cover` turns that page
  off (the cover blocks then open page 1 as a title block, for reports that start straight
  into the summary), aligns the stack and gives it a background colour or image. For art
  or a logo on the cover, pin a cover block to the page (`set_block_layout`). A block you
  insert right after a cover block stays on the cover.
- `set_typography` changes this document's fonts, sizes, colours and hyphenation
  (`hyphens`: auto for justified text, so lines don't show rivers) on top of its theme;
  `set_header_footer` sets the running header and footer ({page}, {pages}, {title}).
  Prefer the theme; override when the author asks for a look, a brand or a match.
- When the author uploads a document to match ("style"), call `match_upload_style`, then
  compare `view_upload` with `view_pages` and refine the cover, headings and callouts.
- If a request needs a capability you don't have (for example generating an image or
  browsing when you have no such tool), say so plainly in a comment reply or in chat.
  Never pretend.
- Match the brief's writing style, audience, platform and language.
"""

_MAX_IMAGES = 8  # e.g. a whole reference spread, or several rendered pages


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


def _turn_note(cfg: Any, messages: list) -> tuple[int, str]:
    """``(index of this turn's human message, note)``: what the author did to the document
    since the agent's previous turn ended, or "" (nothing happened, the agent's first turn on
    this document, or not a document thread). The note is computed once per turn and then
    repeated unchanged for every model call of that turn. Where the previous turn ended is
    kept in the store, so the note survives a restart."""
    idx = next((i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], HumanMessage)), -1)
    if idx < 0:
        return idx, ""
    try:
        from .store import get_store
        from .tools import changes_note

        conf = (cfg or {}).get("configurable", {})
        doc_id = conf.get("codraft_doc_id") or conf.get("thread_id")
        store = get_store()
        if not doc_id:
            return idx, ""
        m = messages[idx]
        key = getattr(m, "id", None) or f"{idx}:{hash(str(m.content))}"
        now = time.time()
        st = store.agent_turn(doc_id)
        if st is None:
            store.get_document(doc_id)  # not a document thread: no state, no note
            store.set_agent_turn(doc_id, key, "", now)
            return idx, ""
        note = st["note"]
        if st["key"] != key:  # a new turn: everything after the last model call of the previous one
            note = changes_note(doc_id, st["last_call"])
        store.set_agent_turn(doc_id, key, note, now)
        return idx, note
    except Exception:  # noqa: BLE001 - no store, or the document is gone: the turn goes on without a note
        return idx, ""


def _tool_images(m: Any) -> list[str]:
    if not isinstance(m, ToolMessage):
        return []
    art = getattr(m, "artifact", None) or {}
    return list(art.get("images") or []) if isinstance(art, dict) else []


def _with_images(messages: list) -> list:
    """The turn's messages with each batch of tool images shown right after the tool results
    that produced it, not appended at the end. Appended images read as a fresh user message
    on every later model call in the turn, and agents kept re-reacting to them ("Now I can
    see the pages…") in a loop. In place, they are history. Only this turn's newest
    ``_MAX_IMAGES`` images are sent."""
    start = 0
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            start = i + 1
            break
    budget = _MAX_IMAGES
    keep: dict[int, list[str]] = {}  # index of a batch's last ToolMessage -> its images
    i = len(messages) - 1
    while i >= start and budget > 0:
        if isinstance(messages[i], ToolMessage):
            end = i
            batch: list[str] = []
            while i >= start and isinstance(messages[i], ToolMessage):
                batch = _tool_images(messages[i]) + batch
                i -= 1
            if batch:
                keep[end] = batch[-budget:]
                budget -= len(keep[end])
            continue
        i -= 1
    if not keep:
        return list(messages)
    out: list = []
    for idx, m in enumerate(messages):
        out.append(m)
        if idx in keep:
            img = _image_message(keep[idx])
            if img is not None:
                out.append(img)
    return out


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
    return HumanMessage(content=[{"type": "text", "text": "[Images returned by the tool calls above]"}] + blocks)


class CodraftMiddleware(AgentMiddleware):
    """Co-author prompt, tools and page images for a ``create_agent`` agent."""

    def __init__(self, *, extra_prompt: str = "", attach_images: bool = True, tools: list | None = None,
                 tool_errors: bool = True, turn_notes: bool = True):
        """``tool_errors``: when any tool (yours or Codraft's) raises, hand the error back to
        the model as the tool's result instead of ending the turn, so one failing lookup
        doesn't throw away a half-drafted document. ``turn_notes``: start each turn by
        telling the agent what the author changed since its last one."""
        super().__init__()
        self.tools = list(CODRAFT_TOOLS if tools is None else tools)
        self.extra_prompt = extra_prompt
        self.attach_images = attach_images
        self.tool_errors = tool_errors
        self.turn_notes = turn_notes

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
        msgs = list(request.messages)
        out = _with_images(msgs) if self.attach_images else msgs
        if self.turn_notes:
            # Before the author's message, so the request is the last thing the model reads.
            # Images only ever go after it, so the index holds in ``out`` too.
            idx, note = _turn_note(cfg, msgs)
            if note:
                out = [*out[:idx], HumanMessage(content=note), *out[idx:]]
        if len(out) != len(msgs):
            overrides["messages"] = out
        return request.override(**overrides)

    def wrap_model_call(self, request, handler):  # type: ignore[override]
        return handler(self._prepare(request))

    async def awrap_model_call(self, request, handler):  # type: ignore[override]
        return await handler(self._prepare(request))

    def wrap_tool_call(self, request, handler):  # type: ignore[override]
        if not self.tool_errors:
            return handler(request)
        try:
            return handler(request)
        except Exception as exc:  # noqa: BLE001
            return _tool_error(request, exc)

    async def awrap_tool_call(self, request, handler):  # type: ignore[override]
        if not self.tool_errors:
            return await handler(request)
        try:
            return await handler(request)
        except Exception as exc:  # noqa: BLE001
            return _tool_error(request, exc)


def _tool_error(request: Any, exc: Exception) -> ToolMessage:
    """A failed tool call as a result the model can read and work around. Interrupts and
    other graph control flow are re-raised untouched."""
    from langgraph.errors import GraphBubbleUp

    if isinstance(exc, GraphBubbleUp):
        raise exc
    call = getattr(request, "tool_call", None) or {}
    name = call.get("name", "tool")
    msg = f"{type(exc).__name__}: {exc}"[:600]
    return ToolMessage(
        content=f"ERROR: {name} failed ({msg}). Carry on without it: try another source or tool, "
                "or tell the author what you couldn't do.",
        tool_call_id=call.get("id", ""), name=name, status="error",
    )
