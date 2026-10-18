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
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool, tool

from . import fonts, stylematch, textutil
from .pages import MM, column_width, content_box, page_px
from .store import (
    BLOCK_TYPES,
    BLOCK_VARIANTS,
    DEFAULT_CONTENT,
    Invalid,
    NotFound,
    get_store,
    plain_text,
)

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
    # Agents often send structured content as a JSON string ({"markdown": …, "tone": …} for a
    # callout, {"title": …} for references). Accept it when it uses this block type's own
    # keys; anything else is literal text (Markdown that happens to start with "{" stays text).
    st = s.strip()
    if st.startswith("{") and block_type in DEFAULT_CONTENT:
        try:
            data = json.loads(st)
        except ValueError:
            data = None
        if isinstance(data, dict) and set(data) & set(DEFAULT_CONTENT[block_type]):
            if block_type == "heading" and isinstance(data.get("text"), str):
                data["text"] = data["text"].lstrip("#").strip()
            return data
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
        pw, ph = page_px(s)
        cw, chh = content_box(s)
        m = s["margins"]
        ncol = int(s["columns"])
        colw = column_width(s)
        gap = s.get("column_gap", 8) * MM
        cols = ", ".join(f"col {i}: x={round(m['left'] * MM + i * (colw + gap))}" for i in range(ncol))
        lines.append(f"Geometry (px, 96/in): page {round(pw)}x{round(ph)}; content box x={round(m['left'] * MM)} "
                     f"y={round(m['top'] * MM)} w={round(cw)} h={round(chh)}; column width {round(colw)}; {cols}.")
        lines.append("Variants: " + "; ".join(f"{t}: {', '.join(v)}" for t, v in BLOCK_VARIANTS.items()))
        lines.extend(_design_lines(s))
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
            if lay.get("variant"):
                bits.append(f"variant={lay['variant']}")
            if lay.get("dropcap"):
                bits.append("dropcap")
            if lay.get("pin"):
                bits.append(f"pinned p{lay['pin']['page']}" + (" (page-anchored)" if lay["pin"].get("anchor") == "page" else "")
                            + (f" repeat={lay['repeat']}" if lay.get("repeat") else ""))
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
    variant: str | None = None, role: str | None = None,
) -> str:
    """Propose a new block. block_type is one of: text, heading, image, video, table,
    chart, callout, divider, references, page-break, spacer. A callout's tone (content
    JSON {"markdown", "tone"}) is info, note, success, warn or dark (white on black). It is inserted after `after_block_id`,
    or at the start if at_start=True, or at the end otherwise. `content` uses the
    same formats as propose_edit. full_width=True makes it span all columns.
    role: "cover" or "back" puts it on the cover or back page; a block inserted right
    after a cover block stays on the cover without it."""
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
            "layout": {"span": "full" if full_width else "column", **({"variant": variant} if variant else {}),
                       **({"role": role} if role else {})},
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
    variant: str | None = None, dropcap: bool | None = None,
    page_x_px: int | None = None, page_y_px: int | None = None,
    page_w_px: int | None = None, page_h_px: int | None = None,
) -> str:
    """Change a block's layout (styling; applies immediately). full_width spans all
    columns. height_px fixes its height (auto_height=True clears it). role: flow,
    cover or back (cover and back pages are exempt from columns). pin_page/pin_top_px
    pin it to a column position so other blocks flow around it; unpin=True releases it.
    page_x_px/page_y_px/page_w_px/page_h_px (with pin_page) instead anchor it to the
    PAGE in px from the page's top-left corner, so it can bleed past the margins (hero
    art, a full-width band, a footer in the bottom margin). On the cover or back page
    (role cover/back) a pin places the block on that page and pin_page is ignored: use
    page_*_px for cover art or a logo; the other cover blocks stack around it. repeat ('all', 'odd', 'even'
    or comma-separated page numbers) repeats a pinned block, for headers and footers;
    text in repeated blocks may use {page}, {pages} and {title}.
    variant sets the typographic style (get_document lists them per block type), e.g.
    text: lead, pullquote, kicker, byline, small, caption, footer, bio; heading: display,
    section, rule, label; image/chart/table: figure-box. dropcap=True gives a text block a
    large initial letter. variant='none' clears it."""
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
        if variant is not None:
            lay["variant"] = None if variant.strip().lower() in ("", "none", "default") else variant.strip()
        if dropcap is not None:
            lay["dropcap"] = bool(dropcap)
        if unpin:
            lay["pin"] = None
        elif page_x_px is not None or page_y_px is not None or page_w_px is not None:
            cur = store.get_block(block_id)["layout"].get("pin") or {}
            cur = cur if cur.get("anchor") == "page" else {}
            lay["pin"] = {"anchor": "page", "page": pin_page or cur.get("page", 1),
                          "x": page_x_px if page_x_px is not None else cur.get("x", 0),
                          "y": page_y_px if page_y_px is not None else cur.get("y", 0),
                          "w": page_w_px if page_w_px is not None else cur.get("w", 300),
                          "h": page_h_px if page_h_px is not None else cur.get("h", 200)}
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
    page_w_mm: float | None = None, page_h_mm: float | None = None,
    margin_top_mm: float | None = None, margin_right_mm: float | None = None,
    margin_bottom_mm: float | None = None, margin_left_mm: float | None = None,
    page_number_start: int | None = None, number_cover_pages: bool | None = None,
    unnumbered_pages: str | None = None,
) -> str:
    """Change document-wide styling (applies immediately): theme (see get_document
    for the list), columns 1-3, page_size (letter, a4, legal, a5, slide; or page_w_mm +
    page_h_mm for a custom size), orientation, per-side margins, page_number_start (the
    number {page} prints on the first numbered page), number_cover_pages (count cover and
    back pages; default no), unnumbered_pages ("7" or "3, 7": positions whose page-number
    footer is hidden and that don't use up a number),
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
        sides = {"top": margin_top_mm, "right": margin_right_mm, "bottom": margin_bottom_mm, "left": margin_left_mm}
        if any(v is not None for v in sides.values()):
            cur = store.get_document(doc_id)["settings"]["margins"]
            s["margins"] = {**cur, **s.get("margins", {}), **{k: float(v) for k, v in sides.items() if v is not None}}
        if page_w_mm is not None or page_h_mm is not None:
            cur = store.get_document(doc_id)["settings"]
            s["page_size"] = "custom"
            s["page_w_mm"] = float(page_w_mm or cur.get("page_w_mm") or 215.9)
            s["page_h_mm"] = float(page_h_mm or cur.get("page_h_mm") or 279.4)
        if page_number_start is not None:
            s["page_number_start"] = int(page_number_start)
        if number_cover_pages is not None:
            s["number_cover_pages"] = bool(number_cover_pages)
        if unnumbered_pages is not None:
            s["unnumbered_pages"] = [int(v) for v in re.findall(r"\d+", unnumbered_pages)]
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


@tool
def reserve_area(config: RunnableConfig, page: int, x_px: int, y_px: int, w_px: int, h_px: int,
                 repeat: str | None = None, label: str = "") -> str:
    """Keep part of a page empty so flowing text avoids it, for example a column's top
    half under hero art, a margin for a later figure, or white space in a layout. The
    rectangle is in px from the page's top-left corner (see get_document geometry). It
    prints as blank space; editors see it hatched. repeat: 'all', 'odd', 'even' or page
    numbers. Styling, so it applies directly. Returns the spacer block id."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        lay: dict[str, Any] = {"pin": {"anchor": "page", "page": int(page), "x": int(x_px), "y": int(y_px),
                                       "w": int(w_px), "h": int(h_px)}}
        if repeat:
            r = repeat.strip().lower()
            lay["repeat"] = r if r in ("all", "odd", "even") else [int(v) for v in re.findall(r"\d+", r)]
        b = store.insert_block(doc_id, "spacer", {"label": label}, layout=lay, author="agent",
                               note="reserved area")
        return f"Reserved {w_px}x{h_px}px at ({x_px},{y_px}) on page {page}: {b['id']}"
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def _view_upload(upload_id: str, config: RunnableConfig = None) -> tuple[str, Any]:
    try:
        store = get_store()
        doc_id = _doc_id(config)
        u = store.get_upload(upload_id, with_text=True)
        if u["doc_id"] != doc_id:
            return (f"ERROR: upload {upload_id} belongs to another document", None)
        look = f"\nIts look: {stylematch.summary(u['style'])}" if u.get("style") else ""
        if u["mime"].startswith("image/") and not u["mime"].endswith("svg+xml"):
            return (f"{u['filename']} ({u['purpose']}) is attached to this turn as an image.{look}",
                    {"images": [u["path"]]})
        text = u["text"][:3000]
        pages = [x for x in u.get("pages", []) if Path(x).exists()][:4]
        if pages:
            return (f"{u['filename']} ({u['purpose']}): its first {len(pages)} page(s) are attached as images.{look}"
                    f"\nText ({len(u['text'])} chars):\n{text or '(no extractable text)'}", {"images": pages})
        return (f"{u['filename']} ({u['purpose']}, {len(u['text'])} chars):{look}\n{text or '(no extractable text)'}", None)
    except Exception as exc:  # noqa: BLE001
        return (_err(exc), None)


view_upload = StructuredTool.from_function(
    func=_view_upload,
    name="view_upload",
    description=("Look at an uploaded file. Images (for example reference pages whose look you should "
                 "match, or photos) are attached so you can see them; text files return their opening text."),
    response_format="content_and_artifact",
)


_SVG_BAD = re.compile(r"(?is)<script.*?</script>|<foreignObject.*?</foreignObject>|\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')")


def sanitize_svg(svg: str) -> str:
    """Strip scripts, event handlers, foreignObject and external references from SVG."""
    s = svg.strip()
    if not s.startswith("<svg") and "<svg" in s:
        s = s[s.index("<svg"):]
    if not s.startswith("<svg") or "</svg>" not in s:
        raise ValueError("expected a complete <svg>...</svg> document")
    s = _SVG_BAD.sub("", s)
    s = re.sub(r'(?i)(xlink:href|href)\s*=\s*"(?!#|data:image/)[^"]*"', "", s)
    if "xmlns=" not in s[:200]:
        s = s.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    return s


@tool
def draw_figure(
    svg: str, config: RunnableConfig, block_id: str | None = None, after_block_id: str | None = None,
    label: str = "", title: str = "", source: str = "", caption: str = "", full_width: bool = False,
    figure_box: bool = True, note: str = "",
) -> str:
    """Draw a diagram yourself as SVG (crisp vector text: use it for labelled diagrams,
    layered architectures, maps, flowcharts, timelines; use generate_image for photos and
    illustrations). Give a complete <svg viewBox=...> with explicit fills; keep fonts
    generic (Arial, Helvetica, sans-serif). It lands in `block_id` (an image block) or as
    a new image block after `after_block_id` (end of document if neither). label (for
    example "FIGURE 1"), title, source and caption fill the figure's frame; figure_box
    gives it the grey figure panel."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        clean = sanitize_svg(svg)
        up = store.add_upload(doc_id, f"figure-{len(clean)}.svg", clean.encode("utf-8"), purpose="asset",
                              mime="image/svg+xml")
        content = {"src": up["id"], "alt": title or label or "figure", "label": label, "title": title,
                   "source": source, "caption": caption}
        variant = {"variant": "figure-box"} if figure_box else {}
        if block_id:
            b = store.get_block(block_id)
            if b["type"] != "image":
                return f"ERROR: {block_id} is a {b['type']} block; draw into an image block or insert a new one"
            s = store.propose(doc_id, "edit", {"content": content, "layout": variant}, block_id=block_id,
                              note=note or f"Figure: {title or label}")
        else:
            s = store.propose(doc_id, "insert", {
                "type": "image", "content": content, "after": after_block_id or "__end__",
                "layout": {"span": "full" if full_width else "column", **variant}}, note=note or f"Figure: {title or label}")
        return _sugg_result(s)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def insert_blocks(blocks: list[dict], config: RunnableConfig, after_block_id: str | None = None,
                  note: str = "") -> str:
    """Insert several blocks in one call (drafting a section, an outline, a whole page).
    Each item: {"type": "text"|"heading"|..., "content": <same formats as propose_edit, or a
    content dict>, "level": 1-3 for headings, "layout": {"span": "full"|"column",
    "variant": "...", "dropcap": true, "pin": {...}, "repeat": "odd", "role": "cover"}, "references":
    [{title, url, note}]}. They go in order after `after_block_id` (or at the end). Each is
    a suggestion; with approval "auto" they apply immediately and the new ids come back."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        after = after_block_id or "__end__"
        out = []
        for i, item in enumerate(blocks or []):
            t = item.get("type", "text")
            if t not in BLOCK_TYPES:
                out.append(f"{i}: ERROR unknown type {t!r}")
                continue
            c = parse_content(t, item.get("content", ""))
            if t == "heading" and item.get("level"):
                c["level"] = max(1, min(int(item["level"]), 3))
            payload = {"type": t, "content": c, "after": after, "layout": item.get("layout") or {}}
            if item.get("references"):
                payload["refs"] = item["references"]
            try:
                sg = store.propose(doc_id, "insert", payload, note=note or f"Insert {t}")
            except (Invalid, NotFound, ValueError) as exc:
                out.append(f"{i}: ERROR {exc}")
                continue
            new_id = sg["payload"].get("result", {}).get("block_id")
            if new_id:
                after = new_id  # keep the batch in order
                out.append(f"{i}: {t} -> {new_id}")
            else:
                out.append(f"{i}: {t} suggested ({sg['id']}, waiting for approval)")
        return "\n".join(out) or "Nothing to insert."
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ------------------------------------------------------------------ design
def _design_lines(s: dict) -> list[str]:
    """Cover, style overrides and running header/footer, for the outline."""
    out = []
    cover = s.get("cover") or {}
    if cover.get("enabled", True):
        bits = [f"{k} {cover[k]}" for k in ("align", "background", "image", "text") if cover.get(k)]
        out.append("Cover: blocks with role=cover get their own page" + (f" ({', '.join(bits)})" if bits else ""))
    else:
        out.append("Cover: OFF (cover blocks open page 1 as a full-width title block)")
    if s.get("style"):
        out.append("Style overrides (on top of the theme): " + ", ".join(f"{k}={v}" for k, v in s["style"].items()))
    for part in ("header", "footer"):
        h = s.get(part) or {}
        if any(h.get(k) for k in ("left", "center", "right")):
            out.append(f"{part.title()}: " + " | ".join(h.get(k) or "—" for k in ("left", "center", "right"))
                       + ("" if h.get("first_page", True) else " (not on the first body page)"))
    return out


def _font_stack(v: str) -> str:
    v = v.strip()
    if "," in v or "'" in v or '"' in v:
        return v
    fam = fonts._BY_KEY.get(re.sub(r"[^a-z0-9]", "", v.lower()), v)
    return fonts.css_stack(fam, fonts._KNOWN.get(fam, "sans"))


@tool
def set_typography(
    config: RunnableConfig, font_body: str | None = None, font_heading: str | None = None,
    font_display: str | None = None, font_label: str | None = None, font_size_px: float | None = None,
    line_height: float | None = None, heading_scale: float | None = None, heading_weight: int | None = None,
    heading_case: str | None = None, color_text: str | None = None, color_heading: str | None = None,
    color_muted: str | None = None, color_accent: str | None = None, color_page: str | None = None,
    color_rule: str | None = None, color_callout: str | None = None, color_figure: str | None = None,
    text_align: str | None = None, paragraph_gap_em: float | None = None,
    paragraph_indent_em: float | None = None, reset: bool = False,
) -> str:
    """Override this document's typography and colours on top of its theme (applies
    immediately; only the values you pass change). Fonts: a family name ("Lato",
    "EB Garamond", any Google Fonts family) or a CSS font list; font_display is for display
    headings and kickers, font_label for labels, captions and figure titles. Colours: hex
    like "#002f58"; color_heading colours the headings, color_accent links, drop caps,
    rules and callout edges, color_callout/color_figure the tinted boxes. font_size_px is
    the body size (13-16 is usual). heading_case: none, uppercase or capitalize.
    text_align: left or justify. reset=True clears every override. Prefer the theme;
    override on purpose (a brand, or a document the author asked you to match)."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        if reset:
            store.update_document(doc_id, settings={"style": None})
            return "Typography reset to the theme."
        vals: dict[str, Any] = {
            "font_body": font_body, "font_heading": font_heading, "font_display": font_display,
            "font_label": font_label, "font_size": font_size_px, "line_height": line_height,
            "heading_scale": heading_scale, "heading_weight": heading_weight, "heading_case": heading_case,
            "color_text": color_text, "color_heading": color_heading, "color_muted": color_muted,
            "color_accent": color_accent, "color_page": color_page, "color_rule": color_rule,
            "color_callout": color_callout, "color_figure": color_figure, "text_align": text_align,
            "paragraph_gap": paragraph_gap_em, "paragraph_indent": paragraph_indent_em,
        }
        vals = {k: (_font_stack(v) if k.startswith("font_") and isinstance(v, str) else v)
                for k, v in vals.items() if v is not None}
        if not vals:
            return "Nothing to change: pass at least one value (or reset=True)."
        doc = store.update_document(doc_id, settings={"style": vals})
        return "Style overrides now: " + json.dumps(doc["settings"]["style"])
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def set_cover(
    config: RunnableConfig, enabled: bool | None = None, page: str = "cover", align: str | None = None,
    background: str | None = None, image: str | None = None, text_color: str | None = None,
    clear_background: bool = False, clear_image: bool = False,
) -> str:
    """Design the cover page (or page="back" for the back page); applies immediately.
    enabled=False turns the separate cover off: the cover blocks (title, subtitle, ...) then
    open page 1 as a full-width title block and the body follows right below them;
    enabled=True brings the cover page back. align (top, center, bottom) places the stack
    of cover blocks. background fills the page with a colour ("#002f58"); image fills it
    with an upload id or image URL (cropped to fit). text_color: light (for dark
    backgrounds), dark, or auto. For art or a logo at an exact spot, give a cover block a
    page-anchored pin with set_block_layout. Add blocks to the cover by inserting them
    right after a cover block (or with role="cover")."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        if page not in ("cover", "back"):
            return "ERROR: page must be 'cover' or 'back'"
        c: dict[str, Any] = {}
        if enabled is not None and page == "cover":
            c["enabled"] = bool(enabled)
        if align:
            c["align"] = align.lower()
        if text_color:
            c["text"] = text_color.lower()
        if background or clear_background:
            c["background"] = None if clear_background else background
        if image or clear_image:
            c["image"] = None if clear_image else image
        if not c:
            return "Nothing to change."
        doc = store.update_document(doc_id, settings={page: c})
        has = any(b["layout"].get("role") == page for b in store.list_blocks(doc_id))
        hint = "" if has or page == "cover" and c.get("enabled") is False else (
            f" Note: no block has role={page} yet, so there is no {page} page; insert one with role='{page}'.")
        return f"{page.title()} settings: {json.dumps(doc['settings'][page])}.{hint}"
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def set_header_footer(
    config: RunnableConfig, header_left: str | None = None, header_center: str | None = None,
    header_right: str | None = None, footer_left: str | None = None, footer_center: str | None = None,
    footer_right: str | None = None, first_page: bool | None = None, rule: bool | None = None,
    clear: bool = False,
) -> str:
    """Running header and footer on body pages (never the cover or back page); applies
    immediately. Text for the left, centre and right of each ("" empties a slot); {page},
    {pages}, {title} and {year} fill in on every page. first_page=False hides both on the
    first body page; rule=True draws a hairline under the header and over the footer.
    clear=True removes both. Use this rather than pinned blocks for ordinary headers and
    footers; they sit in the page margins."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        empty = {"left": "", "center": "", "right": ""}
        if clear:
            store.update_document(doc_id, settings={"header": empty, "footer": empty})
            return "Header and footer removed."
        patch: dict[str, dict] = {"header": {}, "footer": {}}
        for part, vals in (("header", (header_left, header_center, header_right)),
                           ("footer", (footer_left, footer_center, footer_right))):
            for slot, v in zip(("left", "center", "right"), vals, strict=True):
                if v is not None:
                    patch[part][slot] = v
            if first_page is not None:
                patch[part]["first_page"] = bool(first_page)
            if rule is not None:
                patch[part]["rule"] = bool(rule)
        patch = {k: v for k, v in patch.items() if v}
        if not patch:
            return "Nothing to change."
        doc = store.update_document(doc_id, settings=patch)
        return "\n".join(_design_lines(doc["settings"])[1:]) or "Header and footer are empty."
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def match_upload_style(upload_id: str, config: RunnableConfig) -> str:
    """Make this document look like an uploaded one (a PDF, Word file or page image):
    applies its page size, margins, columns, fonts (mapped to open look-alikes), colours,
    cover and running header/footer, and returns what changed. Afterwards compare the
    upload (view_upload) with the result (view_pages) and refine what numbers can't
    capture: the cover design, heading treatments (variants), callouts, figures."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        u = store.get_upload(upload_id)
        if u["doc_id"] != doc_id:
            return f"ERROR: upload {upload_id} belongs to another document"
        prof = u.get("style")
        if not prof:
            data = Path(u["path"]).read_bytes()
            images = [u["path"]] if u["mime"].startswith("image/") else []
            prof = stylematch.analyze(u["filename"], data, u["mime"], images)
            if prof is None:
                return f"ERROR: can't read the style of {u['filename']} (use a PDF, a .docx or a page image)"
            store.set_upload_meta(upload_id, {"pages": u.get("pages", []), "style": prof})
        if prof.get("error"):
            return f"ERROR: could not read the style of {u['filename']}: {prof['error']}"
        patch, changes = stylematch.to_settings(prof)
        if not patch:
            return f"Nothing readable in {u['filename']}; look at it with view_upload and style by eye."
        store.update_document(doc_id, settings=patch)
        return f"Matched {u['filename']}:\n- " + "\n- ".join(changes)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


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
    set_typography,
    set_cover,
    set_header_footer,
    match_upload_style,
    compile_references,
    view_pages,
    view_sketch,
    view_upload,
    draw_figure,
    insert_blocks,
    reserve_area,
]

TOOL_NAMES = frozenset(t.name for t in CODRAFT_TOOLS)
