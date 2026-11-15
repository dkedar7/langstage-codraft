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
5. sends earlier turns' bulky tool results (fetched pages, old outlines and block reads)
   to the model in a trimmed form. The thread keeps them whole; the model call doesn't
   pay for a web page it read three turns ago on every call of every later turn.
"""
from __future__ import annotations

import base64
import re
import time
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from .tools import CODRAFT_TOOLS, MAX_TURN_IMAGES, begin_turn, turn_state

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
- Never delete or shorten content to solve a layout problem (a short last page, an
  overflow, a page target): not the author's and not your own, in any approval mode.
  That covers sections, figures and above all sources: the reference list and every
  block that cites a source stay (deleting one always waits for the author). Fix layout
  with layout: margins, type size, spacing, block width, columns, a page break, a
  smaller or moved figure. If a target can't be met without cutting, say so, say what
  you would cut, and let the author choose.
- A box never shortens content either: `set_block_layout` refuses a height or a pin's
  box that would cut a block's text off, because what is cut off does not print. Give
  the block the height it names (or auto height), or propose a tighter text.
- To put a block at the foot of its page (a call to action, a closing band, a footer
  note) use `set_block_layout(anchor="bottom")`: it stays in the flow. Never pin a block
  or reserve an area for that.
- A large gap before a chart, figure, picture, row or tiles that didn't fit: turn
  back-fill on (`set_document_style(backfill=True)`), so the text after it fills the gap
  and the figure opens the next page. It is on for new documents and off for older ones.
- Design history: every change to the look, yours and the author's, is a step the
  author can undo in Design -> History, and a whole turn of your styling is one step. So
  restyle freely when asked, but go back only when the author asks to ("undo that",
  "the old cover was better"): `list_design_history`, then `restore_design`. Never
  restore a look on your own, and never restyle again what the author just undid.
- Length: `get_document` and `read_blocks` give word counts, and `propose_edit` reports
  the words before and after. When the author asks for a length ("tighten by a third",
  "under 150 words"), work out the target number first, check each edit's count against
  it and revise until it is within about 10% of the target. Say the numbers when you
  report; never call a length target met without checking the counts.
- A deleted block is not gone: `list_deleted_blocks` shows the recently deleted ones and
  `restore_block` puts one back where it was, with its history. Use them when the
  author asks for something back, or when a delete turns out to be a mistake.
- Comments: when asked to address comments, call `list_comments` and handle the open
  ones strictly top to bottom. Tie each change to its comment (`comment_id`) and reply
  with `reply_to_comment`. Resolve a comment only when no approval is pending. A comment
  that says "throughout", "everywhere" or "all …", or that sits on the document rather
  than a block, applies to every block it fits: find them (`search_document`, the
  outline) and propose one change per block, each with that `comment_id`.
  Honor preferences in resolved comments. `read_blocks` marks a block HISTORY when the
  author revised it or resolved a comment on it: call `get_block_history` for those
  blocks only, never for a block that merely has an open comment.
- Cite sources in `references` on the block you change (title, url, note). Use
  `search_document` for uploaded reference material. Use any research tools you have:
  find pages with a search tool, then open the results. Do not guess URLs: a made-up
  address usually fails, and a run of failures stops further guesses. Without a search
  tool, open only URLs the author, the document or an upload gives you.
  Cite only pages you opened, under the authors and title the page itself gives. If a
  search or a fetch fails, say which claims you could not source instead of guessing.
- Blocks with a fixed height state a word budget. Stay roughly within it unless the
  author asks for more.
- When the author drew a sketch on an image block, `view_sketch` shows it. Turn it into
  an image if you have an image tool (e.g. `generate_image`), otherwise say you can't.
- Use `view_pages` to check layout visually (overflow, stranded headings, unbalanced
  columns, clipped captions) after structural or styling changes. It shows at most four
  pages per call and says which pages it left out. A page that looks exactly as it did
  when you last viewed it is not shown again: when nothing changed, looking again won't
  help, so change something or finish.
- Diagrams: `draw_figure` returns a PREVIEW of the figure at its size on the page and a
  LABEL CHECK (labels cut off, too small, overlapping, wider than their box, crossed by
  a line or an arrowhead; arrows drawn on top of each other). Draw simply: a few boxes in a row or a grid, one to three
  words per label, labels inside boxes with room to spare or beside an arrow, never on
  it; put the explanation in the caption. Fix everything the check lists in ONE redraw,
  then look at the preview for meaning (each arrow pointing the right way, each label on
  the right part). Two or three versions is normal and five is the limit: if it still
  isn't right, simplify the figure or tell the author what is wrong with it.
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

_MAX_IMAGES = MAX_TURN_IMAGES  # e.g. a whole reference spread, or several rendered pages

# Earlier turns' tool results, as the model sees them in later turns. Pages and files it
# read keep their opening lines; snapshots of the document are dropped, because the document
# has changed since and the note at the start of the turn says how.
_READ_TOOLS = ("fetch_url", "web_search", "read_upload", "search_document", "view_upload")
_STATE_TOOLS = ("get_document", "read_blocks", "list_comments", "get_block_history", "compile_references",
                "list_deleted_blocks")
_KEEP_READ, _KEEP_STATE, _KEEP_OTHER = 300, 400, 500


def _trim_old_turns(messages: list) -> list:
    """``messages`` with the bulky tool results of earlier turns cut down (see above).
    Everything from this turn's author message on is untouched, and so is every message
    that isn't a tool result. The cut is the same on every call of a turn, so a provider's
    prompt cache still holds from one call to the next."""
    start = next((i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], HumanMessage)), 0)
    out = list(messages)
    for i in range(start):
        m = messages[i]
        if not isinstance(m, ToolMessage) or not isinstance(m.content, str):
            continue
        text, name = m.content, m.name or ""
        if name in _STATE_TOOLS and len(text) > _KEEP_STATE:
            new = (f"[{name} result from an earlier turn, {len(text)} characters, not repeated: the document has "
                   f"changed since. Call {name} again for the current state.]")
        elif name in _READ_TOOLS and len(text) > _KEEP_READ * 2:
            new = (text[:_KEEP_READ] + f"\n[… {len(text) - _KEEP_READ} more characters not repeated: you read this "
                   "in an earlier turn. Open it again if you need the detail.]")
        elif name not in _READ_TOOLS and name not in _STATE_TOOLS and len(text) > _KEEP_OTHER * 3:
            new = text[:_KEEP_OTHER] + f"\n[… {len(text) - _KEEP_OTHER} more characters from an earlier turn not repeated.]"
        else:
            continue
        out[i] = m.model_copy(update={"content": new})
    return out


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


def _start_turn(cfg: Any, messages: list) -> None:
    """Tell the tools which turn this is (the author's latest message), so their per-turn
    limits and memory (redraws of a figure, failed fetches, page images already shown)
    start afresh with each one."""
    idx = next((i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], HumanMessage)), -1)
    if idx < 0:
        return
    conf = (cfg or {}).get("configurable", {}) if isinstance(cfg, dict) else {}
    m = messages[idx]
    doc_id = conf.get("codraft_doc_id") or conf.get("thread_id")
    begin_turn(doc_id, getattr(m, "id", None) or f"{idx}:{hash(str(m.content))}")
    # URLs the author wrote are theirs to have opened, however many guesses failed before.
    if isinstance(m.content, str) and "http" in m.content:
        turn_state(doc_id)["urls"].update(u.rstrip("/.,;)") for u in re.findall(r"https?://[^\s<>\"']+", m.content))


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
                 tool_errors: bool = True, turn_notes: bool = True, trim_history: bool = True):
        """``tool_errors``: when any tool (yours or Codraft's) raises, hand the error back to
        the model as the tool's result instead of ending the turn, so one failing lookup
        doesn't throw away a half-drafted document. ``turn_notes``: start each turn by
        telling the agent what the author changed since its last one. ``trim_history``: send
        earlier turns' long tool results (fetched pages, old outlines) to the model cut
        down; the thread itself keeps them whole."""
        super().__init__()
        self.tools = list(CODRAFT_TOOLS if tools is None else tools)
        self.extra_prompt = extra_prompt
        self.attach_images = attach_images
        self.tool_errors = tool_errors
        self.turn_notes = turn_notes
        self.trim_history = trim_history

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
        _start_turn(cfg, msgs)
        base = _trim_old_turns(msgs) if self.trim_history else msgs
        out = _with_images(base) if self.attach_images else base
        if self.turn_notes:
            # Before the author's message, so the request is the last thing the model reads.
            # Images only ever go after it, so the index holds in ``out`` too.
            idx, note = _turn_note(cfg, msgs)
            if note:
                out = [*out[:idx], HumanMessage(content=note), *out[idx:]]
        if len(out) != len(msgs) or any(a is not b for a, b in zip(out, msgs, strict=False)):
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
