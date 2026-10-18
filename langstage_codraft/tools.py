"""The agent's toolset: ``CODRAFT_TOOLS``.

Every tool resolves the current document from the run's ``thread_id`` (codraft runs one
agent thread per document, and the thread id *is* the document id) and goes through the
same :class:`~langstage_codraft.store.Store` the UI uses. So the agent has no private
path, and anything it can do, a human can do in the editor.

Content writes (``propose_*``) are *suggestions*: they wait for the author's approval
unless the document's approval mode is ``auto``. Styling (block layout, theme, columns,
page setup) is the agent's to drive and applies directly.
"""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool, tool

from . import textutil
from .pages import column_width, content_box
from .store import BLOCK_TYPES, Invalid, NotFound, get_store, plain_text

_MAX_READ = 6000


def _doc_id(config: RunnableConfig | None) -> str:
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    tid = cfg.get("codraft_doc_id") or cfg.get("thread_id")
    if not tid:
        raise ValueError("no document in context: codraft tools must run on a document's thread")
    get_store().get_document(tid)  # raises NotFound with a clear message
    return tid


def _err(exc: Exception) -> str:
    return f"ERROR: {exc}"


def _block_word_budget(settings: dict, block: dict) -> int | None:
    h = block["layout"].get("height")
    pin = block["layout"].get("pin")
    if pin and pin.get("height"):
        h = pin["height"]
    if not h or block["type"] not in ("text", "callout"):
        return None
    w = column_width(settings)
    if block["layout"].get("span") == "full":
        w = content_box(settings)[0]
    return textutil.word_budget(w, h)


def parse_content(block_type: str, content: Any) -> dict:
    """Turn the agent's ``content`` argument into a block content dict."""
    if isinstance(content, dict):
        return content
    s = "" if content is None else str(content)
    if block_type in ("text",):
        return {"markdown": s}
    if block_type == "callout":
        return {"markdown": s}
    if block_type == "heading":
        return {"text": s.lstrip("#").strip()}
    if block_type == "table":
        st = s.strip()
        if st.startswith("|"):
            rows = []
            for line in st.splitlines():
                line = line.strip()
                if not line.startswith("|"):
                    continue
                cells = [c.strip() for c in line.strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    continue
                rows.append(cells)
            return {"rows": rows, "header": True}
        data = json.loads(st)
        return data if isinstance(data, dict) else {"rows": data, "header": True}
    if block_type == "chart":
        data = json.loads(s)
        return data if "spec" in data else {"spec": data}
    if block_type in ("image", "video"):
        st = s.strip()
        if st.startswith("{"):
            return json.loads(st)
        return {"src": st}
    if block_type == "references":
        return {"title": s or "References"}
    return {}


def _content_for_agent(block: dict) -> str:
    c = block["content"]
    t = block["type"]
    if t in ("text", "callout"):
        extra = f" (tone: {c.get('tone')})" if t == "callout" else ""
        return c.get("markdown", "") + extra
    if t == "heading":
        return f"{'#' * int(c.get('level', 1))} {c.get('text', '')}"
    if t == "table":
        rows = c.get("rows", [])
        if not rows:
            return "(empty table)"
        lines = ["| " + " | ".join(map(str, rows[0])) + " |"]
        if c.get("header", True):
            lines.append("|" + "---|" * len(rows[0]))
        lines += ["| " + " | ".join(map(str, r)) + " |" for r in rows[1:]]
        return "\n".join(lines) + (f"\nCaption: {c['caption']}" if c.get("caption") else "")
    if t in ("chart", "image", "video"):
        if t == "image" and c.get("sketch"):
            return json.dumps(c) + "\n(The author drew a sketch on this block: call view_sketch to see it.)"
        return json.dumps(c)
    return json.dumps(c) if c else f"({t})"


def _placement(doc_id: str) -> dict[str, list[int]]:
    rep = get_store().layout_report(doc_id) or {}
    return rep.get("blocks", {}) if isinstance(rep, dict) else {}


# --------------------------------------------------------------------------- read
@tool
def get_document(config: RunnableConfig) -> str:
    """Get the current document's settings, the author's brief (style, objective,
    platform, audience, language, output format) and an outline of every block:
    id, type, page(s), word count, an excerpt, open comments and pending suggestions.
    Call this first. It is cheap and does not return full block text; use
    read_blocks for that."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        doc = store.full(doc_id)
        s = doc["settings"]
        place = _placement(doc_id)
        open_c: dict[str, int] = {}
        for c in doc["comments"]:
            if c["status"] == "open":
                open_c[c["block_id"] or "_doc"] = open_c.get(c["block_id"] or "_doc", 0) + 1
        pend = {sg["block_id"] for sg in doc["suggestions"] if sg["block_id"]}
        lines = [
            f"Document {doc_id}: {doc['title']!r}",
            f"Layout: {s['mode']}, {s['page_size']} {s['orientation']}, {s['columns']} column(s), "
            f"theme {s['theme']}, margins {s['margins']} mm, block gap {s['block_gap']} mm. "
            f"Approval: {s['approval']} ({'your edits wait for the author' if s['approval'] == 'required' else 'your edits apply immediately'}).",
            "Brief: " + ("; ".join(f"{k}={v}" for k, v in s.get("brief", {}).items() if v) or "(none)"),
        ]
        if place.get("_pages"):
            lines.append(f"Rendered pages: {place['_pages']}")
        if open_c.get("_doc"):
            lines.append(f"Document-level open comments: {open_c['_doc']}")
        lines.append("Blocks (reading order):")
        for i, b in enumerate(doc["blocks"], 1):
            text = plain_text(b["type"], b["content"])
            lay = b["layout"]
            bits = [f"{i}. [{b['id']}] {b['type']}"]
            if b["type"] == "heading":
                bits.append(f"h{b['content'].get('level', 1)}")
            if lay.get("role") != "flow":
                bits.append(f"role={lay['role']}")
            if lay.get("span") == "full":
                bits.append("full-width")
            if lay.get("pin"):
                bits.append(f"pinned p{lay['pin']['page']}" + (f" repeat={lay['repeat']}" if lay.get("repeat") else ""))
            if b["id"] in place:
                bits.append(f"page {'-'.join(map(str, sorted(set(place[b['id']]))))}")
            budget = _block_word_budget(s, b)
            if budget:
                bits.append(f"fixed height ~{budget} words max")
            bits.append(f"{textutil.word_count(text)}w")
            if open_c.get(b["id"]):
                bits.append(f"{open_c[b['id']]} open comment(s)")
            if b["id"] in pend:
                bits.append("pending suggestion")
            if b["refs"]:
                bits.append(f"{len(b['refs'])} ref(s)")
            lines.append(" ".join(bits) + (f" — {textutil.excerpt(text, 90)}" if text else ""))
        if not doc["blocks"]:
            lines.append("(no blocks yet)")
        if doc["uploads"]:
            lines.append("Uploads: " + ", ".join(
                f"[{u['id']}] {u['filename']} ({u['purpose']}, {u['chars']} chars)" for u in doc["uploads"]))
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def read_blocks(block_ids: list[str], config: RunnableConfig) -> str:
    """Read the full content, references and layout of specific blocks by id.
    Text is Markdown; tables are Markdown tables; charts are Vega-Lite JSON."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        s = store.get_document(doc_id)["settings"]
        out, total = [], 0
        for bid in block_ids:
            try:
                b = store.get_block(bid)
            except NotFound as e:
                out.append(f"[{bid}] not found ({e})")
                continue
            body = _content_for_agent(b)
            total += len(body)
            if total > _MAX_READ * 3:
                out.append(f"[{bid}] (skipped: read fewer blocks at once)")
                continue
            head = f"[{bid}] {b['type']} · {b['n_versions']} version(s) · layout {json.dumps(b['layout'])}"
            budget = _block_word_budget(s, b)
            if budget:
                head += f" · fits ~{budget} words"
            refs = "".join(f"\n  ref: {r['title']} {r['url']} {r['note']}".rstrip() for r in b["refs"])
            out.append(f"{head}\n{body}{refs}")
        return "\n\n".join(out) or "(no block ids given)"
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def search_document(query: str, config: RunnableConfig, include_uploads: bool = True) -> str:
    """Keyword-search the document's blocks and (optionally) its uploaded reference
    files. Returns the best-matching excerpts with block or upload ids and offsets."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        hits = []
        for b in store.list_blocks(doc_id):
            text = plain_text(b["type"], b["content"])
            sc = textutil.score(query, text)
            if sc > 0:
                hits.append((sc, f"block [{b['id']}] {b['type']}: {textutil.excerpt(text, 220)}"))
        if include_uploads:
            for u in store.list_uploads(doc_id):
                full = store.get_upload(u["id"], with_text=True)["text"]
                for off, ch in textutil.chunks(full):
                    sc = textutil.score(query, ch)
                    if sc > 0:
                        hits.append((sc * 0.9, f"upload [{u['id']}] {u['filename']} @{off}: {textutil.excerpt(ch, 300)}"))
        hits.sort(key=lambda h: -h[0])
        return "\n".join(h[1] for h in hits[:8]) or f"No matches for {query!r}."
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def read_upload(upload_id: str, config: RunnableConfig, offset: int = 0, length: int = 4000) -> str:
    """Read part of an uploaded file's extracted text (reference material or a template)."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        u = store.get_upload(upload_id, with_text=True)
        if u["doc_id"] != doc_id:
            return f"ERROR: upload {upload_id} belongs to another document"
        length = max(200, min(int(length), _MAX_READ))
        text = u["text"][offset: offset + length]
        more = len(u["text"]) - (offset + len(text))
        return (f"{u['filename']} ({u['purpose']}), chars {offset}-{offset + len(text)} of {len(u['text'])}"
                f"{f'; {more} more' if more > 0 else ''}:\n{text or '(no extractable text)'}")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def list_comments(config: RunnableConfig, include_resolved: bool = False) -> str:
    """List the document's comments in document order, top to bottom. Address open
    comments in exactly this order. Resolved comments (include_resolved=True) record
    the author's past preferences; honor them."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        cs = store.list_comments(doc_id, include_resolved=include_resolved)
        if not cs:
            return "No open comments." if not include_resolved else "No comments."
        blocks = {b["id"]: b for b in store.list_blocks(doc_id)}
        lines = []
        for i, c in enumerate(cs, 1):
            where = "document" if not c["block_id"] else f"block [{c['block_id']}]"
            b = blocks.get(c["block_id"]) if c["block_id"] else None
            ex = f" on “{textutil.excerpt(plain_text(b['type'], b['content']), 60)}”" if b else ""
            lines.append(f"{i}. [{c['id']}] ({c['status']}) {where}{ex} — {c['author']}: {c['body']}")
            for r in c["replies"]:
                lines.append(f"     ↳ {r['author']}: {r['body']}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def get_block_history(block_id: str, config: RunnableConfig) -> str:
    """A block's version history (who changed what, with notes) and its resolved
    comments, so you can respect preferences the author expressed earlier."""
    try:
        store = get_store()
        _doc_id(config)
        tree = store.versions(block_id)
        lines = [f"Block {block_id}: {len(tree['versions'])} version(s), "
                 f"{len(tree['branches'])} branch tip(s); head = {tree['head']}"]
        for v in tree["versions"][-12:]:
            mark = "*" if v["id"] == tree["head"] else " "
            lines.append(f"{mark} {v['id']} by {v['author']}: {v['note'] or '-'}")
        b = store.get_block(block_id)
        for c in store.list_comments(b["doc_id"], include_resolved=True, block_id=block_id):
            if c["status"] == "resolved":
                lines.append(f"resolved comment: {c['body']}" + "".join(
                    f" / {r['author']}: {r['body']}" for r in c["replies"]))
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# -------------------------------------------------------------------------- write
def _sugg_result(s: dict) -> str:
    if s["status"] == "applied":
        res = s["payload"].get("result", {})
        return f"Applied ({s['kind']}{' -> ' + res['block_id'] if res.get('block_id') else ''}); suggestion {s['id']}."
    return (f"Suggestion {s['id']} ({s['kind']}) is waiting for the author's approval. "
            "Do not repeat it; move on.")


@tool
def propose_edit(
    block_id: str, content: str, config: RunnableConfig, note: str = "",
    references: list[dict] | None = None, comment_id: str | None = None,
) -> str:
    """Propose new content for an existing block. `content` is the COMPLETE new
    content: Markdown for text and callout blocks, plain text for headings, a Markdown
    table (or JSON {rows, header, caption}) for tables, Vega-Lite JSON for charts,
    JSON {src, alt, caption} for images. `references`: list of {title, url, note} that
    replaces the block's sources. `note`: one line saying why, shown to the author.
    `comment_id`: the comment this addresses. Returns the suggestion id."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        b = store.get_block(block_id)
        payload: dict[str, Any] = {"content": parse_content(b["type"], content)}
        if references is not None:
            payload["refs"] = references
        return _sugg_result(store.propose(doc_id, "edit", payload, block_id=block_id,
                                          comment_id=comment_id, note=note))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def propose_insert(
    block_type: str, content: str, config: RunnableConfig, after_block_id: str | None = None,
    at_start: bool = False, note: str = "", references: list[dict] | None = None,
    full_width: bool = False, heading_level: int = 2, comment_id: str | None = None,
) -> str:
    """Propose a new block. block_type is one of: text, heading, image, video, table,
    chart, callout, divider, references, page-break. It is inserted after `after_block_id`,
    or at the start if at_start=True, or at the end otherwise. `content` uses the
    same formats as propose_edit. full_width=True makes it span all columns."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        if block_type not in BLOCK_TYPES:
            return f"ERROR: unknown block type {block_type!r}; use one of {', '.join(BLOCK_TYPES)}"
        c = parse_content(block_type, content)
        if block_type == "heading":
            c["level"] = max(1, min(int(heading_level), 3))
        payload: dict[str, Any] = {
            "type": block_type, "content": c,
            "after": None if at_start else (after_block_id or "__end__"),
            "layout": {"span": "full" if full_width else "column"},
        }
        if references:
            payload["refs"] = references
        return _sugg_result(store.propose(doc_id, "insert", payload, comment_id=comment_id, note=note))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def propose_delete(block_id: str, config: RunnableConfig, note: str = "",
                   comment_id: str | None = None) -> str:
    """Propose removing a block (the author approves)."""
    try:
        store = get_store()
        return _sugg_result(store.propose(_doc_id(config), "delete", {}, block_id=block_id,
                                          comment_id=comment_id, note=note))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def propose_move(block_id: str, config: RunnableConfig, after_block_id: str | None = None,
                 note: str = "", comment_id: str | None = None) -> str:
    """Propose moving a block to just after `after_block_id` (None = to the start)."""
    try:
        store = get_store()
        return _sugg_result(store.propose(_doc_id(config), "move", {"after": after_block_id},
                                          block_id=block_id, comment_id=comment_id, note=note))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def reply_to_comment(comment_id: str, message: str, config: RunnableConfig, resolve: bool = False) -> str:
    """Reply to a comment, e.g. to say what you changed, or to explain that you lack
    the tool a request needs. resolve=True closes it. Leave it open if the author
    still has to approve a suggestion."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        c = store.get_comment(comment_id)
        if c["doc_id"] != doc_id:
            return f"ERROR: comment {comment_id} belongs to another document"
        c = store.reply_comment(comment_id, message, author="agent", resolve=resolve)
        return f"Replied to {comment_id}" + (" and resolved it." if resolve else ".")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ------------------------------------------------------------------------ styling
@tool
def set_block_layout(
    block_id: str, config: RunnableConfig, full_width: bool | None = None,
    height_px: int | None = None, auto_height: bool = False, role: str | None = None,
    pin_page: int | None = None, pin_top_px: int | None = None, pin_column: int = 0,
    pin_span: int = 1, unpin: bool = False, repeat: str | None = None,
) -> str:
    """Change a block's layout (styling; applies immediately). full_width spans all
    columns. height_px fixes its height (auto_height=True clears it). role: flow,
    cover or back (cover and back pages are exempt from columns). pin_page/pin_top_px
    pin it to a page position so other blocks flow around it; unpin=True releases it.
    repeat ('all', 'odd', 'even' or comma-separated page numbers) repeats a pinned
    block, for headers, footers and logos."""
    try:
        store = get_store()
        _doc_id(config)
        lay: dict[str, Any] = {}
        if full_width is not None:
            lay["span"] = "full" if full_width else "column"
        if auto_height:
            lay["height"] = None
        elif height_px is not None:
            lay["height"] = int(height_px)
        if role:
            lay["role"] = role
        if unpin:
            lay["pin"] = None
        elif pin_page is not None or pin_top_px is not None:
            cur = store.get_block(block_id)["layout"].get("pin") or {}
            lay["pin"] = {**cur, "page": pin_page or cur.get("page", 1),
                          "top": pin_top_px if pin_top_px is not None else cur.get("top", 0),
                          "column": pin_column, "span": pin_span}
        if repeat is not None:
            r = repeat.strip().lower()
            lay["repeat"] = None if r in ("", "none") else (
                r if r in ("all", "odd", "even") else [int(x) for x in re.findall(r"\d+", r)])
        b = store.set_layout(block_id, lay)
        return f"Layout of {block_id} is now {json.dumps(b['layout'])}"
    except (Invalid, NotFound, ValueError) as exc:
        return _err(exc)


@tool
def set_document_style(
    config: RunnableConfig, theme: str | None = None, columns: int | None = None,
    page_size: str | None = None, orientation: str | None = None, mode: str | None = None,
    margin_mm: float | None = None, block_gap_mm: float | None = None,
    column_gap_mm: float | None = None, title: str | None = None,
) -> str:
    """Change document-wide styling (applies immediately): theme (see get_document
    for the list), columns 1-3, page_size (letter, a4, legal, a5, slide), orientation,
    mode (paged or pageless), uniform margin in mm, the inter-block gap, the column gap,
    or the title."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        s: dict[str, Any] = {}
        if theme:
            from .themes import theme_names

            if theme not in theme_names():
                return f"ERROR: unknown theme {theme!r}; available: {', '.join(theme_names())}"
            s["theme"] = theme
        if columns is not None:
            s["columns"] = int(columns)
        if page_size:
            s["page_size"] = page_size.lower()
        if orientation:
            s["orientation"] = orientation.lower()
        if mode:
            s["mode"] = mode.lower()
        if margin_mm is not None:
            m = float(margin_mm)
            s["margins"] = {"top": m, "right": m, "bottom": m, "left": m}
        if block_gap_mm is not None:
            s["block_gap"] = float(block_gap_mm)
        if column_gap_mm is not None:
            s["column_gap"] = float(column_gap_mm)
        doc = store.update_document(doc_id, title=title, settings=s or None)
        st = doc["settings"]
        return (f"Style updated: {st['mode']}, {st['page_size']} {st['orientation']}, "
                f"{st['columns']} col, theme {st['theme']}.")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------- references
@tool
def compile_references(config: RunnableConfig) -> str:
    """List every source cited by any block, de-duplicated and numbered in reading
    order. Use it to answer "list the references"; to put the list in the document,
    insert a `references` block, which renders this list automatically."""
    try:
        store = get_store()
        seen: dict[str, int] = {}
        lines = []
        for b in store.list_blocks(_doc_id(config)):
            for r in b["refs"]:
                key = (r.get("url") or r["title"]).lower()
                if key in seen:
                    continue
                seen[key] = len(seen) + 1
                lines.append(f"[{seen[key]}] {r['title']}" + (f". {r['url']}" if r.get("url") else "")
                             + (f" ({r['note']})" if r.get("note") else "") + f"  — cited by {b['id']}")
        return "\n".join(lines) or "No block cites any source yet."
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# -------------------------------------------------------------------------- vision
def _view_pages(pages: list[int] | None = None, config: RunnableConfig = None) -> tuple[str, Any]:
    try:
        doc_id = _doc_id(config)
        from .render import get_renderer

        r = get_renderer()
        if r is None:
            return ("view_pages is unavailable: no renderer is running (the server starts one).", None)
        shots = r.call(r.screenshot_pages(doc_id, pages=pages, max_pages=4))
        if not shots:
            return ("Nothing to show: the document has no pages yet.", None)
        desc = ", ".join(f"page {s['page']}" for s in shots)
        text = (f"Rendered {desc} of {shots[0]['total']}. The images are attached to this turn. "
                "Look for overflow, orphaned headings, awkward gaps and crowded columns.")
        return (text, {"images": [s["path"] for s in shots]})
    except Exception as exc:  # noqa: BLE001
        return (_err(exc), None)


view_pages = StructuredTool.from_function(
    func=_view_pages,
    name="view_pages",
    description=(
        "See the document as the author sees it: screenshots of the rendered pages "
        "(default: the first 4; pass page numbers to choose). Use it to judge layout, "
        "for example overflowing blocks, unbalanced columns or a heading stranded at the "
        "bottom of a page. Requires a vision-capable model; images arrive with the result."
    ),
    response_format="content_and_artifact",
)


def _sketch_path(doc_id: str, block_id: str) -> str | None:
    """Filesystem path of an image block's sketch (an uploaded PNG), or None."""
    store = get_store()
    b = store.get_block(block_id)
    if b["doc_id"] != doc_id:
        raise ValueError(f"block {block_id} is not in this document")
    if b["type"] != "image":
        raise ValueError(f"block {block_id} is a {b['type']} block; sketches live on image blocks")
    sk = b["content"].get("sketch")
    if not sk:
        return None
    return store.get_upload(sk)["path"]


def _view_sketch(block_id: str, config: RunnableConfig = None) -> tuple[str, Any]:
    try:
        p = _sketch_path(_doc_id(config), block_id)
        if p is None:
            return (f"Block {block_id} has no sketch.", None)
        return ("The author's sketch is attached to this turn. Turn it into an image with an "
                "image tool if you have one (then propose_edit the block's src), or describe "
                "what it shows. If you have no image tool, say so.", {"images": [p]})
    except Exception as exc:  # noqa: BLE001
        return (_err(exc), None)


view_sketch = StructuredTool.from_function(
    func=_view_sketch,
    name="view_sketch",
    description="See the sketch the author drew on an image block (a rough drawing to turn into a real image).",
    response_format="content_and_artifact",
)


CODRAFT_TOOLS = [
    get_document,
    read_blocks,
    search_document,
    read_upload,
    list_comments,
    get_block_history,
    propose_edit,
    propose_insert,
    propose_delete,
    propose_move,
    reply_to_comment,
    set_block_layout,
    set_document_style,
    compile_references,
    view_pages,
    view_sketch,
]

TOOL_NAMES = frozenset(t.name for t in CODRAFT_TOOLS)
