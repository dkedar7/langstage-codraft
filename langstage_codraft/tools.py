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

import ast
import difflib
import hashlib
import json
import math
import operator
import re
import time
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
    VARIANT_NOTES,
    Invalid,
    NoChange,
    NotFound,
    get_store,
    plain_text,
)

_MAX_READ = 6000
# Block types that span all columns unless told otherwise: their cells need the width.
_WIDE = ("row", "stat")
_MAX_PAGES = 4  # page images per view_pages call
MAX_TURN_IMAGES = 8  # images the middleware shows the model in one turn (the newest ones)
_MAX_DRAWS = 5  # versions of one figure in a turn; the third and later come with a warning
_SPLIT_WORDS = 150  # a text draft longer than this, in two or more paragraphs, becomes one block each

# What the agent has done in the current turn, per document: how often it redrew a figure,
# which page images it has been shown, how many fetches failed. The middleware starts a new
# turn (begin_turn) at each author message; without it (an MCP client, a bring-your-own
# graph) the state simply expires after a quiet spell.
_TURNS: dict[str, dict] = {}
_TURN_IDLE = 900.0


def _fresh_turn(key: Any) -> dict:
    return {"key": key, "at": time.time(), "n": {}, "shown": [], "urls": set()}


def turn_state(doc_id: str | None) -> dict:
    """The current turn's scratch state for a document (see ``_TURNS``)."""
    k = doc_id or "_"
    st = _TURNS.get(k)
    if st is None or time.time() - st["at"] > _TURN_IDLE:
        st = _TURNS[k] = _fresh_turn(st["key"] if st else None)
    st["at"] = time.time()
    return st


def begin_turn(doc_id: str | None, key: Any) -> None:
    """Called by the middleware on every model call with the turn's key (its author
    message): a new key starts a new turn."""
    k = doc_id or "_"
    st = _TURNS.get(k)
    if st is None or st["key"] != key:
        _TURNS[k] = _fresh_turn(key)


def _bump(st: dict, name: str) -> int:
    st["n"][name] = st["n"].get(name, 0) + 1
    return st["n"][name]


def _thread_id(config: RunnableConfig | None) -> str | None:
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    return cfg.get("codraft_doc_id") or cfg.get("thread_id")


def _doc_id(config: RunnableConfig | None) -> str:
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    tid = cfg.get("codraft_doc_id") or cfg.get("thread_id")
    if not tid:
        raise ValueError("no document in context: codraft tools must run on a document's thread")
    get_store().get_document(tid)  # raises NotFound with a clear message
    return tid


def _err(exc: Exception) -> str:
    if isinstance(exc, NoChange):  # not a failure: nothing needed recording
        return str(exc.args[0] if exc.args else exc)
    if isinstance(exc, KeyError) and exc.args:  # NotFound: str() would add quotes
        return f"ERROR: {exc.args[0]}"
    return f"ERROR: {exc}"


_ID_KINDS = {"blk": "block", "sug": "suggestion", "cmt": "comment", "upl": "upload", "ver": "version",
             "doc": "document"}


def _pending_insert(store: Any, doc_id: str, ref: str) -> dict | None:
    """The pending insert suggestion ``ref`` names, or None. Raises a NotFound that says
    what the id is when it is a suggestion that can't stand in for a block."""
    if not isinstance(ref, str) or not ref.startswith("sug_"):
        return None
    try:
        sg = store.get_suggestion(ref)
    except NotFound:
        sg = None
    if sg is None or sg["doc_id"] != doc_id:
        raise NotFound(f"no suggestion {ref} in this document; call get_document for the current outline")
    p = sg["payload"] if isinstance(sg["payload"], dict) else {}
    if sg["status"] == "pending" and sg["kind"] == "insert":
        return sg
    if sg["status"] == "pending":
        raise NotFound(f"{ref} is your pending {sg['kind']} of block {sg['block_id']}, not a block: "
                       f"use {sg['block_id']}")
    if sg["status"] in ("accepted", "applied"):
        bid = (p.get("result") or {}).get("block_id") or sg["block_id"]
        gone = " (the block was deleted since)" if sg["kind"] == "delete" else ""
        raise NotFound(f"{ref} is a suggestion the author already accepted; its block is {bid}{gone}. "
                       f"Use the block id")
    why = p.get("reject_reason") or p.get("obsolete_reason") or ""
    raise NotFound(f"{ref} is a suggestion that was {sg['status']}{f' ({why})' if why else ''}; there is no "
                   "such block. Call get_document for the current outline")


def _block(store: Any, doc_id: str, block_id: str) -> dict:
    """The block, or a NotFound whose message helps the agent recover: what kind of id it
    passed, whether the block was deleted, and the closest ids in this document."""
    try:
        return store.block_in(doc_id, block_id)
    except Invalid:
        raise
    except NotFound:
        pass
    ref = str(block_id)
    prefix = ref.split("_", 1)[0]
    if prefix == "sug":
        if _pending_insert(store, doc_id, ref):
            raise NotFound(f"{ref} is a pending insert (still waiting for the author), not a block yet. "
                           "read_blocks, propose_edit, propose_delete, set_block_layout and after_block_id "
                           "accept it; this tool needs an accepted block") from None
    elif prefix in _ID_KINDS and prefix != "blk":
        raise NotFound(f"{ref} is a {_ID_KINDS[prefix]} id, not a block id (blocks are blk_…)") from None
    row = store._one("SELECT doc_id, deleted FROM blocks WHERE id = ?", (ref,))
    if row is not None and row["doc_id"] == doc_id and row["deleted"]:
        raise NotFound(f"block {ref} was deleted (restore_block brings it back); call get_document for the "
                       "current outline") from None
    ids = [b["id"] for b in store.list_blocks(doc_id)]
    near = difflib.get_close_matches(ref, ids, n=3, cutoff=0.75)
    hint = f" Closest ids: {', '.join(near)}." if near else ""
    raise NotFound(f"no block {ref} in this document.{hint} Ids are never guessable: copy them from "
                   "get_document") from None


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


# ------------------------------------------------------------- cut-off content (gh #58)
# Block types whose content a fixed height or a pin's box can cut off (the print view's CLIPS).
_CLIP_TYPES = frozenset({"text", "callout", "row", "stat", "heading", "table", "references"})


def _box_px(layout: dict | None) -> float | None:
    """The height a layout fixes for its block: the pin's box, else ``height``; None = it grows."""
    lay = layout or {}
    pin = lay.get("pin") or {}
    return (pin.get("h") if pin.get("anchor") == "page" else pin.get("height")) or lay.get("height") or None


def _has_boxed_text(store: Any, doc_id: str) -> bool:
    return any(b["type"] in _CLIP_TYPES and _box_px(b["layout"]) for b in store.list_blocks(doc_id))


def cut_off_blocks(doc_id: str) -> dict[str, dict] | None:
    """Blocks whose content the print view cuts off (taller than their fixed height, their
    pin's box or the page), by block id, as the export would print them. None when no
    renderer can say (no server and no Chromium)."""
    from .render import ensure_renderer

    try:
        r = ensure_renderer()
        if r is None or not hasattr(r, "cut_off"):
            return None
        return {c["block_id"]: c for c in r.call(r.cut_off(doc_id))}
    except Exception:  # noqa: BLE001 - no browser or a failed render; the caller falls back
        return None


def _cut_line(c: dict) -> str:
    need = (f": its content needs about {c['natural_px']} px, the box is {c['box_px']} px"
            if c.get("natural_px") and c.get("box_px") else "")
    return f"{c['block_id']} (page {c['page']}{need})"


def _cut_note(store: Any, doc_id: str) -> str:
    """After a document-wide style change: which fixed-height blocks the change leaves cut
    off, so the agent fixes them in the same turn. "" when none (or nothing has a fixed box)."""
    if not _has_boxed_text(store, doc_id):
        return ""
    cut = cut_off_blocks(doc_id)
    if not cut:
        return ""
    return (" OVERFLOW: " + "; ".join(_cut_line(c) for c in list(cut.values())[:6])
            + ". Content that is cut off does not print: give those blocks room "
            "(set_block_layout auto_height=True or a larger box) or undo this change. Never leave the "
            "author's content cut off.")


def _layout_cut_check(store: Any, doc_id: str, before: dict, after: dict) -> str | None:
    """After the agent changed an accepted block's layout: None when the block's content
    still prints in full, else the reason to refuse the change (the caller puts the old
    layout back). A layout change must never hide content (gh #58): shortening what the
    document prints goes through a suggestion the author approves, not through a box."""
    box = _box_px(after["layout"])
    if after["type"] not in _CLIP_TYPES or not box or after["layout"] == before["layout"]:
        return None
    was = _box_px(before["layout"])
    bid = after["id"]
    cut = cut_off_blocks(doc_id)
    if cut is None:
        # No renderer to measure with: refuse only what is plainly too small for its words.
        budget = _block_word_budget(store.get_document(doc_id)["settings"], after)
        words = _words(after)
        if budget is None or words <= 2 * budget + 12:
            return None
        return (f"REFUSED, nothing changed: a box of {int(box)} px holds about {budget} words and {bid} has "
                f"{words}, so most of it would not print. A layout change must not hide content: use "
                "auto_height=True or a taller box; to make the block shorter, propose a tighter text "
                "(propose_edit) and let the author decide.")
    c = cut.get(bid)
    if c is None:
        return None
    if was and box >= was - 0.5 and before["layout"].get("pin") == after["layout"].get("pin"):
        # It already had this box (or a smaller one), the author's own choice. Don't refuse an
        # unrelated restyle, unless it is this restyle that made the content outgrow the box.
        store.set_layout(bid, before["layout"], replace=True)
        earlier = cut_off_blocks(doc_id)
        still = earlier is None or bid in earlier
        store.set_layout(bid, after["layout"], replace=True)
        if still:
            return None
    need = f"its content needs about {c['natural_px']} px" if c.get("natural_px") else "its content is taller"
    return (f"REFUSED, nothing changed: in a box of {int(box)} px {bid} would be cut off ({need}), and what "
            "is cut off does not print. A layout change must not hide content: use auto_height=True"
            + (f" or a height of at least {c['natural_px']} px" if c.get("natural_px") else " or a taller box")
            + "; to make the block shorter, propose a tighter text (propose_edit) and let the author decide.")


def parse_content(block_type: str, content: Any) -> dict:
    """Turn the agent's ``content`` argument into a block content dict."""
    if isinstance(content, dict):
        return content
    if isinstance(content, list) and block_type in ("row", "stat"):
        return {"cells" if block_type == "row" else "items": content}
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
                data = {**_heading(data["text"]), **{k: v for k, v in data.items() if k != "text"}}
            return data
    if block_type in ("text",):
        return {"markdown": s}
    if block_type == "callout":
        return {"markdown": s}
    if block_type == "heading":
        return _heading(s)
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
    if block_type == "row":
        return _row(s)
    if block_type == "stat":
        return _stat(s)
    return {}


def _json_list(s: str) -> list | None:
    if not s.strip().startswith("["):
        return None
    try:
        data = json.loads(s)
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def _row(s: str) -> dict:
    """A row's content from what the agent typed: a JSON list of cells, or Markdown with a
    line of ``---`` between the cells."""
    cells = _json_list(s)
    if cells is None:
        cells = [part.strip() for part in re.split(r"(?m)^[ \t]*-{3,}[ \t]*$", s)]
        cells = [c for c in cells if c]
    if len(cells) < 2:
        raise Invalid('a row needs 2 to 4 cells: send {"cells": ["first", "second", ...]} (each cell is '
                      "Markdown), or Markdown with a line of --- between the cells")
    return {"cells": cells}


def _stat(s: str) -> dict:
    """Number tiles from what the agent typed: a JSON list of {value, label} (or [value,
    label] pairs), or one tile per line as ``value | label``."""
    items = _json_list(s)
    if items is None:
        items = []
        for line in s.splitlines():
            line = line.strip().strip("|").strip()
            if not line or re.fullmatch(r"[:\-| ]+", line):
                continue
            value, _, label = line.partition("|")
            items.append({"value": value.strip(), "label": label.strip()})
    if not items:
        raise Invalid('number tiles need at least one item: send {"items": [{"value": "99.9%", "label": '
                      '"uptime"}, ...]}, or one tile per line as "value | label"')
    return {"items": items}


def _heading(text: str) -> dict:
    """Heading content from what the agent typed: "## Method" is a level-2 heading, so the
    level can be set by writing it the Markdown way."""
    m = re.match(r"\s*(#{1,6})\s+(.*)", text, re.S)
    if m:
        return {"text": m.group(2).strip(), "level": min(len(m.group(1)), 3)}
    return {"text": text.lstrip("#").strip()}


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


def _words(block: dict) -> int:
    return textutil.word_count(plain_text(block["type"], block["content"]))


def _history_hint(store: Any, block: dict) -> str:
    """Says when a block has history worth reading before a rewrite (the author revised it,
    or resolved a comment on it); "" otherwise, which is most blocks."""
    if str(block.get("id", "")).startswith("sug_"):
        return ""
    bits = []
    try:
        if int(block.get("n_versions") or 1) > 1:
            vs = store.versions(block["id"])["versions"]
            mine = sum(1 for v in vs[1:] if v["author"] not in ("agent", "auto"))
            if mine:
                bits.append(f"the author revised it {mine} time(s)")
        done = sum(1 for c in store.list_comments(block["doc_id"], include_resolved=True, block_id=block["id"])
                   if c["status"] == "resolved")
        if done:
            bits.append(f"{done} resolved comment(s)")
    except Exception:  # noqa: BLE001 - a hint, never a reason to fail a read
        return ""
    return f" · HISTORY: {' and '.join(bits)}; get_block_history before rewriting it" if bits else ""


_NOT_PROSE = re.compile(r"\s*([-*+]\s|\d+[.)]\s|\||>|#{1,6}\s|```|~~~|<|!\[|\s{4,})")


def split_paragraphs(markdown: str) -> list[str]:
    """A text draft as one piece per paragraph. A list, a table, a quote, code, a picture
    or raw HTML stays with the paragraph before it, and a fenced code block is never cut."""
    chunks, cur, fence = [], [], False
    for line in str(markdown).replace("\r\n", "\n").split("\n"):
        if re.match(r"\s*(```|~~~)", line):
            fence = not fence
        if not line.strip() and not fence:
            if cur:
                chunks.append("\n".join(cur))
                cur = []
            continue
        cur.append(line)
    if cur:
        chunks.append("\n".join(cur))
    out: list[str] = []
    for c in chunks:
        if out and _NOT_PROSE.match(c):
            out[-1] += "\n\n" + c
        else:
            out.append(c)
    return out


def _should_split(block_type: str, content: dict, layout: dict | None = None) -> list[str] | None:
    """The paragraphs a long text draft should be split into, or None to keep it whole."""
    lay = layout or {}
    if block_type != "text" or lay.get("height") or lay.get("pin") or lay.get("variant") or lay.get("dropcap"):
        return None
    md = content.get("markdown")
    if not isinstance(md, str) or textutil.word_count(md) <= _SPLIT_WORDS:
        return None
    parts = split_paragraphs(md)
    return parts if len(parts) >= 2 else None


def _placement(doc_id: str) -> dict[str, list[int]]:
    rep = get_store().layout_report(doc_id) or {}
    return rep.get("blocks", {}) if isinstance(rep, dict) else {}


# --------------------------------------------------------------------------- read
@tool
def get_document(config: RunnableConfig) -> str:
    """Get the current document's settings, the author's brief (style, objective,
    platform, audience, language, output format) and an outline of every block:
    id, type, page(s), word count, an excerpt, open comments and pending suggestions.
    Your pending suggestions appear where they will land, marked PENDING (a pending insert
    is listed under its suggestion id). Call this first. It is cheap and does not return
    full block text; use read_blocks for that."""
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
        pv = store.preview(doc_id)
        lines = [
            f"Document {doc_id}: {doc['title']!r}",
            f"Layout: {s['mode']}, {_size_label(s)} {s['orientation']}, {s['columns']} column(s), "
            f"theme {s['theme']}, margins {s['margins']} mm, block gap {s['block_gap']} mm, "
            f"back-fill {'on' if s.get('backfill') else 'off'}. "
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
        lines.append("Variants: "
                     + "; ".join(f"{t}: {', '.join(v)}" for t, v in BLOCK_VARIANTS.items()))
        seen: list[str] = []
        for names in BLOCK_VARIANTS.values():
            seen += [v for v in names if v not in seen]
        lines.append("What each variant looks like: "
                     + "; ".join(f"{v} = {VARIANT_NOTES[v]}" for v in seen if v in VARIANT_NOTES) + ".")
        lines.extend(_design_lines(s))
        if place.get("_pages"):
            lines.append(f"Rendered pages: {place['_pages']}")
        if open_c.get("_doc"):
            lines.append(f"Document-level open comments: {open_c['_doc']}")
        npend = len(doc["suggestions"])
        if npend:
            lines.append(
                f"Blocks (reading order). {npend} suggestion(s) of yours are waiting for the author: they are "
                "shown where they will land, marked PENDING. The author's document does not have them yet. "
                "Do not propose them again.")
        else:
            lines.append("Blocks (reading order):")
        for i, e in enumerate(pv["entries"], 1):
            b = e["block"]
            kinds = {sg["kind"]: sg["id"] for sg in e["pending"]}
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
            if lay.get("fill"):
                bits.append(f"fill={lay['fill']}")
            if b["type"] == "row":
                bits.append(f"{len(b['content'].get('cells') or [])} cells side by side")
            if b["type"] == "stat":
                bits.append(f"{len(b['content'].get('items') or [])} number tiles")
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
            if b["refs"]:
                bits.append(f"{len(b['refs'])} ref(s)")
            if "insert" in kinds:
                bits.append("PENDING INSERT (not in the author's document yet)")
            if "edit" in kinds:
                bits.append(f"PENDING EDIT {kinds['edit']} (the excerpt is your proposed text)")
            if "move" in kinds:
                bits.append(f"PENDING MOVE {kinds['move']} (shown at its new place)")
            if "delete" in kinds:
                bits.append(f"PENDING DELETE {kinds['delete']}")
            lines.append(" ".join(bits) + (f" — {textutil.excerpt(text, 90)}" if text else ""))
        if not pv["entries"]:
            lines.append("(no blocks yet)")
        else:
            total = sum(_words(e["block"]) for e in pv["entries"] if not e["deleted"])
            lines.append(f"Words: {total} in all ({'with your pending suggestions applied' if npend else 'as accepted'}). "
                         "Each block's count is the number before \"w\".")
        for lo in pv["left_out"]:
            lines.append(f"Pending but it will not apply: {lo['id']} ({lo['kind']}"
                         + (f" of {lo['block_id']}" if lo["block_id"] else "") + f"): {lo['reason']}. "
                         "Re-read the block and propose again only if it is still needed.")
        if doc["uploads"]:
            lines.append("Uploads: " + ", ".join(
                f"[{u['id']}] {u['filename']} ({u['purpose']}, {u['chars']} chars)" for u in doc["uploads"]))
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def read_blocks(block_ids: list[str], config: RunnableConfig) -> str:
    """Read the full content, references and layout of specific blocks by id.
    Text is Markdown; tables are Markdown tables; charts are Vega-Lite JSON. A block with
    a pending edit of yours shows both the accepted text and your proposed text; a pending
    insert is read by its suggestion id (sug_…)."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        s = store.get_document(doc_id)["settings"]
        entries = {e["block"]["id"]: e for e in store.preview(doc_id)["entries"]}
        out, total = [], 0

        def refs_of(b: dict) -> str:
            from .refs import format_ref

            return "".join(f"\n  ref [@{r['key']}]: {format_ref(r)}"
                           + (f" (private note: {r['note']})" if r.get("note") else "") for r in b["refs"])

        for bid in block_ids:
            e = entries.get(bid)
            if e is None:
                try:
                    _block(store, doc_id, bid)
                    out.append(f"[{bid}] not found")
                except NotFound as exc:
                    out.append(f"[{bid}] not found ({exc.args[0] if exc.args else exc})")
                except Invalid as exc:
                    out.append(f"[{bid}] ERROR: {exc}")
                continue
            b = e["block"]
            kinds = {sg["kind"]: sg["id"] for sg in e["pending"]}
            body = _content_for_agent(b)
            was = e["accepted"]
            total += len(body) + (len(_content_for_agent(was)) if was else 0)
            if total > _MAX_READ * 3:
                out.append(f"[{bid}] (skipped: read fewer blocks at once)")
                continue
            if "insert" in kinds:
                head = (f"[{bid}] {b['type']} · PENDING INSERT: your suggestion, not in the author's document yet "
                        f"(propose_edit with this id revises it) · layout {json.dumps(b['layout'])}")
            else:
                head = (f"[{bid}] {b['type']} · {b['n_versions']} version(s) · layout {json.dumps(b['layout'])}"
                        + _history_hint(store, b))
            head += f" · {_words(b)} words"
            budget = _block_word_budget(s, b)
            if budget:
                head += f" · fits ~{budget} words"
            if "move" in kinds:
                head += f" · PENDING MOVE {kinds['move']}"
            if "delete" in kinds:
                head += f" · PENDING DELETE {kinds['delete']}"
            if was is not None:
                out.append(f"{head}\nACCEPTED (what the author's document has now, {_words(was)} words):\n"
                           f"{_content_for_agent(was)}"
                           f"{refs_of(was)}\nYOUR PENDING EDIT {kinds.get('edit', '')} (waiting for the author; "
                           f"a new propose_edit replaces it; {_words(b)} words):\n{body}{refs_of(b)}")
            else:
                out.append(f"{head}\n{body}{refs_of(b)}")
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
    comments. Call it only when read_blocks marks the block with HISTORY (the author
    revised it, or resolved a comment on it), to respect what they decided earlier. An open
    comment is not history: list_comments already shows it. Not for routine edits, and
    never for blocks you created."""
    try:
        store = get_store()
        blk = _block(store, _doc_id(config), block_id)
        if not _history_hint(store, blk):
            return (f"Block {block_id} has no history to respect: {blk['n_versions']} version(s), none revised by "
                    "the author, and no resolved comments. Go ahead with the edit. (read_blocks marks the blocks "
                    "that do have history; this call is only needed for those.)")
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
    more = ""
    if s["kind"] == "insert":
        more = (f" Until then the new block is {s['id']}: pass that id as after_block_id to put something "
                "right after it, or to propose_edit to revise it.")
    return (f"Suggestion {s['id']} ({s['kind']}) is waiting for the author's approval. "
            f"Do not repeat it; move on.{more}")


@tool
def propose_edit(
    block_id: str, content: str, config: RunnableConfig, note: str = "",
    references: list[dict] | None = None, comment_id: str | None = None,
    heading_level: int | None = None, keep_together: bool = False,
) -> str:
    """Propose new content for an existing block. `content` is the COMPLETE new
    content: Markdown for text and callout blocks, plain text for headings, a Markdown
    table (or JSON {rows, header, caption, total}) for tables (a cell is one line of Markdown:
    **bold**, *italic*, `code`, links; total=true sets the last row as a total; a long table
    splits across pages and repeats its header row), Vega-Lite JSON for charts,
    JSON {src, alt, caption} for images, JSON {"cells": ["...", "..."]} for a row (each cell
    Markdown; optional "widths": [1, 1, 2]) and JSON {"items": [{"value", "label"}]} for
    number tiles (stat). Markdown is GitHub-flavoured, plus superscript
    x^2^ and subscript H~2~O (attached to the text before, no spaces inside, 40 characters
    at most); <sup>2</sup> / <sub>2</sub> and Unicode characters (m², °C, CO₂) work too.
    Only ~~text~~ strikes out. No maths: LaTeX ($x^2$) prints literally. `references`: list of {title, url,
    authors, year, publisher, accessed, key, note} that replaces the block's sources. The
    reference list prints Authors (Year). Title. Publisher. URL; a reference's `note` is a
    private note and is never printed. Cite a source in the text as [@key] (its `key`, e.g.
    [@smith2020]; [@a; @b] for two): it prints as its number in the list, [3]. `note`: one line saying why, shown to the
    author. `comment_id`: the comment this addresses. heading_level (1-3) changes a
    heading's level; do not type section numbers the document doesn't already use.
    `block_id` may be one of your own pending inserts (sug_…): that revises the pending
    block in place. Sending content identical to what the block has, or to an edit already
    pending, records nothing. Write one paragraph per text block: a long draft of several
    paragraphs sent to a one-paragraph block is split into one block per paragraph. For a
    text or callout block the result gives the word count before and after: check it
    against any length the author asked for. Returns the suggestion id."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        pend = _pending_insert(store, doc_id, block_id)
        if pend is not None:
            t = pend["payload"].get("type", "text")
            c = parse_content(t, content)
            if t == "heading" and heading_level:
                c["level"] = max(1, min(int(heading_level), 3))
            sg = store.revise_suggestion(pend["id"], content=c, refs=references, note=note or None)
            return (f"Revised your pending insert {sg['id']}; it is still waiting for the author's approval. "
                    "Do not repeat it; move on.")
        b = _block(store, doc_id, block_id)
        c = parse_content(b["type"], content)
        if b["type"] == "heading" and heading_level:
            c["level"] = max(1, min(int(heading_level), 3))
        if b["type"] == "row" and isinstance(c.get("cells"), list) and "widths" not in c:
            was = b["content"].get("widths")
            if was and len(was) != len(c["cells"]):
                c["widths"] = None  # a different number of cells: the old widths no longer fit
        payload: dict[str, Any] = {"content": c}
        if references is not None:
            payload["refs"] = references
        # A long multi-paragraph draft into a block that holds one paragraph (a template's
        # placeholder, a stub) becomes one block per paragraph, so the author can approve,
        # comment on and version each. A block the author already keeps as several
        # paragraphs is theirs to structure and is edited whole.
        parts = None if keep_together else _should_split(b["type"], c, b["layout"])
        if parts and len(split_paragraphs(b["content"].get("markdown") or "")) <= 1:
            first = store.propose(doc_id, "edit", {**payload, "content": {**c, "markdown": parts[0]}},
                                  block_id=block_id, comment_id=comment_id, note=note)
            ids, after = [], block_id
            for part in parts[1:]:
                sg = store.propose(doc_id, "insert", {"type": "text", "content": {"markdown": part}, "after": after,
                                                      "layout": {"span": b["layout"].get("span", "column")}},
                                   comment_id=comment_id, note=note or "Next paragraph")
                after = (sg["payload"].get("result") or {}).get("block_id") or sg["id"]
                ids.append(after)
            return (_sugg_result(first) + f" Your draft had {len(parts)} paragraphs, so it was split into one block "
                    f"per paragraph: {block_id} holds the first and the rest follow it in order as "
                    f"{', '.join(ids)}. The author approves and comments on each separately. (keep_together=True "
                    "keeps a draft in one block; use it only when the paragraphs must not be separated.)")
        sg = store.propose(doc_id, "edit", payload, block_id=block_id, comment_id=comment_id, note=note)
        res = _sugg_result(sg)
        if b["type"] in ("text", "callout"):
            # From what the store kept, so a value it repaired (a list of paragraphs) counts too.
            was = _words(b)
            now = _words({"type": b["type"], "content": sg["payload"].get("content") or c})
            if was and now != was:
                res += f" Words: {was} -> {now} ({(now - was) * 100 / was:+.0f}%)."
        return res
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def propose_insert(
    block_type: str, content: str, config: RunnableConfig, after_block_id: str | None = None,
    at_start: bool = False, note: str = "", references: list[dict] | None = None,
    full_width: bool = False, heading_level: int = 2, comment_id: str | None = None,
    variant: str | None = None, role: str | None = None, fill: str | None = None,
) -> str:
    """Propose a new block. block_type is one of: text, heading, image, video, table,
    chart, callout, divider, references, page-break, spacer, row, stat. A callout's tone (content
    JSON {"markdown", "tone"}) is info, note, success, warn or dark (white on black).
    Side by side: `row` puts 2 to 4 Markdown cells next to each other (content JSON
    {"cells": ["**Fast.** ...", "**Safe.** ...", "..."]}, optional "widths": [1, 1, 2];
    variant="cards" boxes each cell); `stat` is a row of number tiles (content JSON
    {"items": [{"value": "99.99%", "label": "delivered in 60 s"}, ...]}). Both are full
    width unless you say otherwise. variant="band" makes a heading a solid header strip and
    a callout a call-to-action box; `fill` ("accent", "text", "muted", "callout", "figure"
    or a hex colour) picks the colour of a band, the row's cards or the tiles, and the text
    on it turns white or dark by itself. Use these, never pins, raw HTML or drawn SVG, for
    columns of points, number tiles, header bands and call-to-action boxes. It is inserted after `after_block_id`,
    or at the start if at_start=True, or at the end otherwise. `content` uses the
    same formats as propose_edit. full_width=True makes it span all columns.
    role: "cover" or "back" puts it on the cover or back page; a block inserted right
    after a cover block stays on the cover without it. `after_block_id` may be a pending
    insert of yours (sug_…), which keeps a run of new blocks in order. For several blocks
    at once use insert_blocks. heading_level applies to headings ("## Title" in content
    sets it too)."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        if block_type not in BLOCK_TYPES:
            return f"ERROR: unknown block type {block_type!r}; use one of {', '.join(BLOCK_TYPES)}"
        c = parse_content(block_type, content)
        if block_type == "heading":
            c.setdefault("level", max(1, min(int(heading_level), 3)))
        wide = full_width or block_type in _WIDE
        payload: dict[str, Any] = {
            "type": block_type, "content": c,
            "after": None if at_start else (after_block_id or "__end__"),
            "layout": {"span": "full" if wide else "column", **({"variant": variant} if variant else {}),
                       **({"role": role} if role else {}), **({"fill": fill} if fill else {})},
        }
        if references:
            payload["refs"] = references
        if not (at_start or comment_id) and _should_split(block_type, c, payload["layout"]):
            # A long multi-paragraph draft: insert_blocks puts each paragraph in its own block.
            return insert_blocks.invoke(
                {"blocks": [{"type": "text", "content": c, "layout": payload["layout"],
                             **({"references": references} if references else {})}],
                 "after_block_id": after_block_id, "note": note}, config=config)
        return _sugg_result(store.propose(doc_id, "insert", payload, comment_id=comment_id, note=note))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def propose_delete(block_id: str, config: RunnableConfig, note: str = "",
                   comment_id: str | None = None) -> str:
    """Propose removing a block (the author approves). Only for content the author asked
    you to remove or that is clearly redundant. Never delete or cut content, yours or the
    author's, to make a layout fit or to reach a page count: not a section, not a figure,
    not sources. Deleting the reference list, or a block that carries sources, always
    waits for the author's approval, even when your other edits apply immediately.
    `block_id` may be one of your own pending inserts (sug_…), which withdraws it."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        pend = _pending_insert(store, doc_id, block_id)
        if pend is not None:
            store.withdraw_suggestion(pend["id"])
            return f"Withdrew your pending insert {pend['id']}; the author will not see it."
        b = _block(store, doc_id, block_id)
        # Sources are the one thing an unattended run must not lose: the reference list and
        # any block that cites something stay until the author says so, in either mode.
        sources = b["type"] == "references" or bool(b["refs"])
        sg = store.propose(doc_id, "delete", {}, block_id=block_id, comment_id=comment_id, note=note,
                           hold=sources)
        if sources and sg["status"] == "pending":
            what = "the reference list" if b["type"] == "references" else f"a block that cites {len(b['refs'])} source(s)"
            return (f"Suggestion {sg['id']} (delete) is WAITING for the author: {block_id} is {what}, and removing "
                    "sources always needs the author's approval, in every approval mode. It is still in the "
                    "document. If you proposed this to save space or reach a page count, that is not a reason to "
                    "drop sources: fix the layout with layout (type size, margins, spacing, columns), tighten "
                    "wording, or tell the author the target can't be met without cutting. Do not repeat it; "
                    "tell the author it is waiting for them.")
        return _sugg_result(sg)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def propose_move(block_id: str, config: RunnableConfig, after_block_id: str | None = None,
                 note: str = "", comment_id: str | None = None) -> str:
    """Propose moving a block to just after `after_block_id` (None = to the start).
    `after_block_id` may be a pending insert of yours (sug_…). To reorder several blocks,
    move each one after the block that should precede it; Accept all applies a chain of
    moves in the right order. Never delete and re-insert a block to move it."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        _block(store, doc_id, block_id)
        return _sugg_result(store.propose(doc_id, "move", {"after": after_block_id},
                                          block_id=block_id, comment_id=comment_id, note=note))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def list_deleted_blocks(config: RunnableConfig) -> str:
    """List this document's recently deleted blocks (most recent first): id, type and the
    first words. A deleted block keeps its content, layout and history; `restore_block`
    brings one back."""
    try:
        store = get_store()
        rows = store.deleted_blocks(_doc_id(config), limit=30)
        if not rows:
            return "No deleted blocks in this document."
        return "Recently deleted (restore_block brings one back where it was):\n" + "\n".join(
            f"- [{r['id']}] {r['type']}, {r['n_versions']} version(s)"
            + (f" — {r['preview']}" if r["preview"] else "") for r in rows)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@tool
def restore_block(block_id: str, config: RunnableConfig) -> str:
    """Bring a deleted block back where it was, with its content, layout and version
    history (ids from `list_deleted_blocks`). It applies immediately in either approval
    mode: it puts the author's own block back and writes nothing new. Use it when the
    author asks for something back, or to undo a delete that was a mistake."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        if block_id not in {r["id"] for r in store.deleted_blocks(doc_id, limit=100000)}:
            _block(store, doc_id, block_id)  # a wrong kind of id, another document's block: say so
            return f"Block {block_id} is not deleted, so there is nothing to restore."
        b = store.restore_block(block_id)
        text = plain_text(b["type"], b["content"])
        return (f"Restored {b['type']} block {b['id']} where it was, with its history"
                + (f" — {textutil.excerpt(text, 90)}" if text else "") + ". This applied immediately (no approval "
                "needed: it is the author's own block as it was). Tell the author you restored it.")
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
    fill: str | None = None, anchor: str | None = None,
) -> str:
    """Change a block's layout (styling; applies immediately). anchor="bottom" sits a
    flowing block at the foot of its page (a call to action, a closing band, a footer note)
    and later blocks flow above it: use this, never a pin, to put something at the bottom
    of a page; anchor="none" returns it to the flow. full_width spans all
    columns. height_px fixes its height (auto_height=True clears it); a height or a pin's box
    that would cut off the block's text, table or cells is refused, because what is cut off
    does not print: to shorten content, propose a tighter text instead. role: flow,
    cover or back (cover and back pages are exempt from columns). pin_page/pin_top_px
    pin it to a column position so other blocks flow around it; unpin=True releases it.
    page_x_px/page_y_px/page_w_px/page_h_px (with pin_page) instead anchor it to the
    PAGE in px from the page's top-left corner, so it can bleed past the margins (hero
    art, an edge-to-edge strip, a footer in the bottom margin). On the cover or back page
    (role cover/back) a pin places the block on that page and pin_page is ignored: use
    page_*_px for cover art or a logo; the other cover blocks stack around it. repeat ('all', 'odd', 'even'
    or comma-separated page numbers) repeats a pinned block, for headers and footers;
    text in repeated blocks may use {page}, {pages} and {title}.
    variant sets the typographic style (get_document lists them per block type), e.g.
    text: lead, pullquote, kicker, byline, small, caption, footer, bio; heading: display,
    section, rule, label, band; image/chart/table: figure-box; callout: band; row: cards;
    stat: plain. dropcap=True gives a text block a large initial letter. variant='none'
    clears it. fill colours a heading or callout (a band), a row's cells or number tiles:
    "accent", "text", "muted", "heading", "callout", "figure", "rule" (the document's theme
    colours) or a hex colour like "#ff5a1f"; the text on it turns white or dark by itself;
    fill='none' clears it. For a header band use a heading with variant="band" and
    full_width=True; for a call-to-action box a callout with variant="band". Do not pin
    blocks to build columns, tiles or bands: insert a row or stat block instead.
    `block_id` may be a pending insert of yours (sug_…): its layout is then part of the
    suggestion."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        pend = _pending_insert(store, doc_id, block_id)
        cur_layout = (pend["payload"].get("layout") or {}) if pend else _block(store, doc_id, block_id)["layout"]
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
        if fill is not None:
            lay["fill"] = None if fill.strip().lower() in ("", "none", "default") else fill.strip()
        if anchor is not None:
            lay["anchor"] = None if anchor.strip().lower() in ("", "none", "flow", "default") else anchor.strip().lower()
        if unpin or lay.get("anchor"):
            lay["pin"] = None
        elif page_x_px is not None or page_y_px is not None or page_w_px is not None:
            cur = cur_layout.get("pin") or {}
            cur = cur if cur.get("anchor") == "page" else {}
            lay["pin"] = {"anchor": "page", "page": pin_page or cur.get("page", 1),
                          "x": page_x_px if page_x_px is not None else cur.get("x", 0),
                          "y": page_y_px if page_y_px is not None else cur.get("y", 0),
                          "w": page_w_px if page_w_px is not None else cur.get("w", 300),
                          "h": page_h_px if page_h_px is not None else cur.get("h", 200)}
        elif pin_page is not None or pin_top_px is not None:
            cur = cur_layout.get("pin") or {}
            lay["pin"] = {**cur, "page": pin_page or cur.get("page", 1),
                          "top": pin_top_px if pin_top_px is not None else cur.get("top", 0),
                          "column": pin_column, "span": pin_span}
        if repeat is not None:
            r = repeat.strip().lower()
            lay["repeat"] = None if r in ("", "none") else (
                r if r in ("all", "odd", "even") else [int(x) for x in re.findall(r"\d+", r)])
        if lay.get("pin"):
            lay.setdefault("anchor", None)   # a pin replaces a bottom anchor
        if pend is not None:
            if lay.get("pin") is None and "pin" in lay:
                lay["repeat"] = None
            sg = store.revise_suggestion(pend["id"], layout=lay)
            return f"Layout of your pending insert {block_id} is now {json.dumps(sg['payload'].get('layout'))}"
        before = _block(store, doc_id, block_id)
        b = store.set_layout(block_id, lay)
        why = _layout_cut_check(store, doc_id, before, b)
        if why:
            store.set_layout(block_id, before["layout"], replace=True)
            return f"ERROR: {why}"
        return f"Layout of {block_id} is now {json.dumps(b['layout'])}"
    except (Invalid, NotFound, ValueError) as exc:
        return _err(exc)


def _size_label(s: dict) -> str:
    """The page size as rendered: a size an older version stored but nothing renders reads as
    the letter it falls back to, not as the name the agent asked for (gh #30)."""
    from .store import PAGE_SIZES

    ps = s.get("page_size")
    return ps if ps in PAGE_SIZES else f"letter (stored size {ps!r} isn't supported)"


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
    unnumbered_pages: str | None = None, backfill: bool | None = None,
) -> str:
    """Change document-wide styling (applies immediately): backfill (true or false: a
    chart, figure, picture, row or tiles that doesn't fit at the bottom of a page lets the
    text after it fill the gap and opens the next page itself; on for new documents, turn it
    on to close a large gap before a figure), theme (see get_document
    for the list), columns 1-3, page_size (letter, a4, legal, a5 or slide only; for any
    other size, e.g. A3 or tabloid, pass page_w_mm + page_h_mm instead), orientation, per-side margins, page_number_start (the
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
        if backfill is not None:
            s["backfill"] = bool(backfill)
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
                f"{st['columns']} col, theme {st['theme']}." + _cut_note(store, doc_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------- references
@tool
def compile_references(config: RunnableConfig) -> str:
    """List every source cited by any block, de-duplicated and numbered in reading
    order. Use it to answer "list the references"; to put the list in the document,
    insert a `references` block, which renders this list automatically."""
    try:
        from .refs import compile_refs, format_ref

        store = get_store()
        lines = [f"[{r['n']}] {format_ref(r)}  — cite as [@{r['key']}]; on {r['block']}"
                 + (f"; private note: {r['note']}" if r.get("note") else "")
                 for r in compile_refs(store.list_blocks(_doc_id(config)))]
        return "\n".join(lines) or "No block cites any source yet."
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# -------------------------------------------------------------------------- vision
def page_shots(renderer: Any, doc_id: str, pages: list[int] | None = None, from_page: int = 1,
               pending: bool = True) -> tuple[str, list[dict]]:
    """Screenshots for view_pages (the agent tool and the MCP tool): the text that goes
    with them and the shots. At most ``_MAX_PAGES`` pages per call; the text says which
    pages were left out and how to ask for them. With ``pending`` and suggestions waiting,
    the render is the preview with those suggestions applied, and the text says so."""
    store = get_store()
    pv = store.preview(doc_id) if pending else None
    preview = bool(pv and pv["pending"])
    wanted = [int(p) for p in pages] if pages else None
    start = max(1, int(from_page or 1))
    if wanted is None and start > 1:
        wanted = list(range(start, start + _MAX_PAGES))
    # preview only when needed: a renderer stand-in without the argument keeps working
    shots = renderer.call(renderer.screenshot_pages(doc_id, pages=wanted, max_pages=_MAX_PAGES,
                                                    **({"preview": True} if preview else {})))
    if not shots:
        if wanted and start > 1:
            return (f"No page {start}: the document is shorter than that. Call view_pages() for the first pages.", [])
        return ("Nothing to show: the document has no pages yet.", [])
    total = shots[0]["total"]
    seen = [s["page"] for s in shots]
    text = f"Rendered page{'s' if len(seen) > 1 else ''} {', '.join(map(str, seen))} of {total}."
    rest = shots[0].get("more") or ([p for p in range(seen[-1] + 1, total + 1)] if not pages else [])
    if rest:
        text += (f" NOT shown: page{'s' if len(rest) > 1 else ''} {rest[0]}"
                 + (f"-{rest[-1]}" if len(rest) > 1 else "")
                 + f" (at most {_MAX_PAGES} per call): call view_pages(from_page={rest[0]}) to see them.")
    cut = [f"{b} on page {sh['page']}" for sh in shots for b in sh.get("overflow", [])]
    if cut:
        text += (f" OVERFLOW: {', '.join(cut)}: the content is taller than the block's box or the page and is "
                 "cut off. Give it more height, shorten it, or let it flow (unpin, auto height).")
    if preview:
        marks = {e["block"]["id"]: "+".join(sg["kind"] for sg in e["pending"])
                 for e in pv["entries"] if e["pending"] and not e["deleted"]}
        per_page = []
        for sh in shots:
            here = [f"{b} ({marks[b]})" for b in sh.get("blocks", []) if b in marks]
            if here:
                per_page.append(f"page {sh['page']}: {', '.join(here)}")
        text += (f" This is a PREVIEW: it shows the document as it will look once your {pv['pending']} pending "
                 "suggestion(s) are accepted. The author's document does not have them yet"
                 + (f". Pending on these pages: {'; '.join(per_page)}" if per_page else "")
                 + ". Pass pending=False to see only what the author has accepted.")
        gone = [e["block"]["id"] for e in pv["entries"] if e["deleted"]]
        if gone:
            text += f" Left out of the preview by a pending delete: {', '.join(gone)}."
    return (text, shots)


def _view_pages(pages: list[int] | None = None, from_page: int = 1, pending: bool = True,
                config: RunnableConfig = None) -> tuple[str, Any]:
    try:
        doc_id = _doc_id(config)
        from .render import get_renderer

        r = get_renderer()
        if r is None:
            return ("view_pages is unavailable: no renderer is running (the server starts one).", None)
        text, shots = page_shots(r, doc_id, pages, from_page, pending)
        if not shots:
            return (text, None)
        # A page that renders exactly as it did the last time the agent looked, and whose
        # image the model still has in this turn, is not sent again.
        st = turn_state(doc_id)
        recent = {h for _, h in st["shown"][-MAX_TURN_IMAGES:]}
        fresh, same = [], []
        for sh in shots:
            try:
                digest = f"p{sh['page']}:" + hashlib.sha1(Path(sh["path"]).read_bytes()).hexdigest()
            except OSError:
                digest = f"p{sh['page']}:{sh['path']}"
            if st["key"] is not None and digest in recent:
                same.append(sh["page"])
            else:
                fresh.append(sh)
                st["shown"].append((sh["path"], digest))
        if same:
            text += (f" Page{'s' if len(same) > 1 else ''} {', '.join(map(str, same))} look"
                     f"{'' if len(same) > 1 else 's'} exactly as when you last viewed "
                     f"{'them' if len(same) > 1 else 'it'} this turn (nothing you did since changed "
                     f"{'them' if len(same) > 1 else 'it'}), so "
                     f"{'they are' if len(same) > 1 else 'it is'} not attached again.")
        if not fresh:
            return (text + " Nothing changed on the pages you asked for: looking again will not help. Change "
                    "something first, or finish.", None)
        text += (" The images are attached to this turn. Look for overflow, orphaned headings, awkward gaps, "
                 "crowded columns, clipped text and labels that overlap.")
        return (text, {"images": [s["path"] for s in fresh]})
    except Exception as exc:  # noqa: BLE001
        return (_err(exc), None)


view_pages = StructuredTool.from_function(
    func=_view_pages,
    name="view_pages",
    description=(
        "See the document as the author sees it: screenshots of the rendered pages. At most 4 "
        "pages per call: by default the first 4; pass `pages` (page numbers) or `from_page` to "
        "see others, and the result says which pages were not shown. While suggestions of yours "
        "are waiting for approval the render is a PREVIEW with them applied (so you can check "
        "your own drafts, figures and moves before the author decides); pending=False shows only "
        "the accepted document. Use it to judge layout, for example overflowing blocks, "
        "unbalanced columns or a heading stranded at the bottom of a page, and after draw_figure "
        "to check a diagram's labels. Requires a vision-capable model; images arrive with the result."
    ),
    response_format="content_and_artifact",
)


def _sketch_path(doc_id: str, block_id: str) -> str | None:
    """Filesystem path of an image block's sketch (an uploaded PNG), or None."""
    store = get_store()
    b = _block(store, doc_id, block_id)
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
                 repeat: str | None = None, label: str = "", remove: bool = False) -> str:
    """Keep part of a page empty so flowing text avoids it, for example a column's top
    half under hero art, a margin for a later figure, or white space in a layout. The
    rectangle is in px from the page's top-left corner (see get_document geometry). It
    prints as blank space; editors see it hatched. repeat: 'all', 'odd', 'even' or page
    numbers. Styling, so it applies directly. Returns the spacer block id. A reserved area
    this rectangle overlaps on the same page is replaced, so calling again moves or resizes
    it; remove=True only removes the reserved areas the rectangle overlaps. To put a block
    at the bottom of a page, don't reserve room: set_block_layout(anchor="bottom")."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        rep: Any = None
        if repeat:
            r = repeat.strip().lower()
            rep = r if r in ("all", "odd", "even") else [int(v) for v in re.findall(r"\d+", r)]
        got = store.reserve_area(doc_id, {"page": int(page), "x": int(x_px), "y": int(y_px), "w": int(w_px),
                                          "h": int(h_px)}, repeat=rep, label=label, remove=remove)
        was = f" (replaced {', '.join(got['replaced'])})" if got["replaced"] and not remove else ""
        if remove:
            return (f"Removed {len(got['replaced'])} reserved area(s): {', '.join(got['replaced'])}" if got["replaced"]
                    else "No reserved area overlaps that rectangle.")
        return f"Reserved {w_px}x{h_px}px at ({x_px},{y_px}) on page {page}: {got['block']['id']}{was}"
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
    return _tame_markers(s)


_ATTR = r"""\b%s\s*=\s*(?:"([^"]*)"|'([^']*)')"""


def _attr(tag: str, name: str) -> str | None:
    m = re.search(_ATTR % re.escape(name), tag)
    return (m.group(1) if m.group(1) is not None else m.group(2)) if m else None


def _num(v: str | None, default: float) -> float:
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", v or "")
    return float(m.group(1)) if m else default


def _tame_markers(svg: str) -> str:
    """Keep arrowheads in proportion to their lines.

    An SVG ``<marker>`` is sized in multiples of the stroke width unless it says
    ``markerUnits="userSpaceOnUse"``. Models draw thick connectors (stroke-width 8 to 12)
    and give the marker a markerWidth of 10 or so, meaning "10 units": the arrowhead comes
    out 80 to 120 units wide and buries the labels. A marker whose head would be more than
    four times as wide as the thickest line using it is rewritten to user units at three
    times that line's width (at least 12), with a viewBox so its drawing scales to fit."""
    def fix(m: re.Match) -> str:
        tag = m.group(0)
        mid = _attr(tag, "id")
        if not mid or (_attr(tag, "markerUnits") or "strokeWidth") != "strokeWidth":
            return tag
        widths = []
        for use in re.finditer(r"<[a-zA-Z][^<>]*\bmarker-(?:end|start|mid)\s*[=:]\s*[\"']?url\(\s*[\"']?#"
                               + re.escape(mid) + r"\b[^<>]*>", svg):
            el = use.group(0)
            sw = _attr(el, "stroke-width")
            if sw is None:
                sm = re.search(r"stroke-width\s*:\s*([\d.]+)", el)
                sw = sm.group(1) if sm else None
            widths.append(_num(sw, 1.0))
        if not widths:
            return tag
        stroke = max(widths)
        mw, mh = _num(_attr(tag, "markerWidth"), 3.0), _num(_attr(tag, "markerHeight"), 3.0)
        if mw * stroke <= max(4 * stroke, 24) and mh * stroke <= max(4 * stroke, 24):
            return tag
        size = max(3 * stroke, 12.0)
        w, h = (size, size * mh / mw) if mw >= mh else (size * mw / mh, size)
        out = tag
        for name, val in (("markerWidth", f"{w:g}"), ("markerHeight", f"{h:g}")):
            if _attr(out, name) is None:
                out = out[:-1].rstrip("/").rstrip() + f' {name}="{val}"' + (">" if not tag.endswith("/>") else "/>")
            else:
                out = re.sub(_ATTR % name, f'{name}="{val}"', out, count=1)
        extra = ' markerUnits="userSpaceOnUse"'
        if _attr(out, "viewBox") is None:
            extra += f' viewBox="0 0 {mw:g} {mh:g}"'
        if _attr(out, "markerUnits") is not None:
            out = re.sub(_ATTR % "markerUnits", "", out, count=1)
        return out[:-1] + extra + ">"

    return re.sub(r"<marker\b[^<>]*>", fix, svg)


def _figure_feedback(doc_id: str, ref: str, preview: bool, svg: str) -> tuple[str, str | None]:
    """What the agent gets back after drawing: the figure as it sits on the page (an image
    path for a vision model) and the label check, as text. ("", None) with no renderer."""
    from .render import ensure_renderer

    try:
        r = ensure_renderer()
        if r is None:
            return (" No renderer is running, so there is no preview: check the figure with view_pages.", None)
        shot = r.call(r.figure_shot(doc_id, ref, preview=preview, svg=svg))
    except Exception as exc:  # noqa: BLE001 - a preview that fails must not lose the figure
        return (f" (No preview: {type(exc).__name__}: {str(exc)[:120]}. Check the figure with view_pages.)", None)
    if shot is None:
        return (" It is not on any page yet, so there is no preview: check it with view_pages.", None)
    text = (f"\nPREVIEW attached: the figure as it sits on page {shot['page']}, {shot['width']} x {shot['height']} px "
            "on the page (shown at twice that size).")
    if shot["issues"]:
        text += (f"\nLABEL CHECK found {len(shot['issues'])} problem(s), measured at the printed size (positions are in "
                 "viewBox units):\n- "
                 + "\n- ".join(shot["issues"])
                 + "\nFix them all in ONE redraw (pass this figure's id as block_id): shorter labels, labels beside "
                   "or above their arrows and never on them, larger boxes, fewer parts.")
    else:
        text += ("\nLABEL CHECK: no cut-off, overlapping, overflowing or crossed labels. Now look at the preview "
                 "for what a check can't see: that each arrow points the right way and each label sits on the "
                 "right part. If it reads correctly, the figure is done.")
    return (text, shot["path"])


def _draw_figure(
    svg: str, block_id: str | None = None, after_block_id: str | None = None,
    label: str = "", title: str = "", source: str = "", caption: str = "", full_width: bool = False,
    figure_box: bool = True, note: str = "", comment_id: str | None = None,
    config: RunnableConfig = None,
) -> tuple[str, Any]:
    try:
        store = get_store()
        doc_id = _doc_id(config)
        st = turn_state(doc_id)
        if block_id and st["n"].get(f"draw:{block_id}", 0) >= _MAX_DRAWS:
            return (f"ERROR: {block_id} has been drawn {_MAX_DRAWS} times this turn; it was not redrawn again. "
                    "More passes are not converging. Leave the last version, or simplify it in your next turn "
                    "(fewer labels, numbered parts explained in the caption), and tell the author plainly what "
                    "is still wrong with the figure.", None)
        clean = sanitize_svg(svg)
        up = store.add_upload(doc_id, f"figure-{len(clean)}.svg", clean.encode("utf-8"), purpose="asset",
                              mime="image/svg+xml")
        content = {"src": up["id"], "alt": title or label or "figure", "label": label, "title": title,
                   "source": source, "caption": caption}
        variant = {"variant": "figure-box"} if figure_box else {}
        pend = _pending_insert(store, doc_id, block_id) if block_id else None
        if pend is not None:
            if pend["payload"].get("type") != "image":
                return (f"ERROR: {block_id} is a pending {pend['payload'].get('type', 'text')} insert, not an image",
                        None)
            sg = store.revise_suggestion(pend["id"], content=content, layout=variant or None,
                                         note=note or f"Figure: {title or label}")
            head = f"Redrew your pending figure {sg['id']}; it is still waiting for the author's approval."
            ref, preview = sg["id"], True
        else:
            if block_id:
                b = _block(store, doc_id, block_id)
                if b["type"] != "image":
                    return (f"ERROR: {block_id} is a {b['type']} block; draw into an image block or insert a "
                            "new one", None)
                s = store.propose(doc_id, "edit", {"content": content, "layout": variant}, block_id=block_id,
                                  comment_id=comment_id, note=note or f"Figure: {title or label}")
            else:
                s = store.propose(doc_id, "insert", {
                    "type": "image", "content": content, "after": after_block_id or "__end__",
                    "layout": {"span": "full" if full_width else "column", **variant}},
                    comment_id=comment_id, note=note or f"Figure: {title or label}")
            head = _sugg_result(s)
            applied = s["status"] == "applied"
            ref = ((s["payload"].get("result") or {}).get("block_id") or block_id) if applied else (
                s["id"] if s["kind"] == "insert" else block_id)
            preview = not applied
        n = _bump(st, f"draw:{ref}")
        feedback, path = _figure_feedback(doc_id, ref, preview, clean)
        if n >= 3:
            feedback += (f"\nThis is version {n} of this figure in this turn ({_MAX_DRAWS} at most). If it still "
                         "has problems, do not keep nudging coordinates: simplify it (fewer labels, numbered parts "
                         "explained in the caption, a legend under the drawing).")
        return (head + f" The figure's id is {ref}." + feedback, {"images": [path]} if path else None)
    except Exception as exc:  # noqa: BLE001
        return (_err(exc), None)


draw_figure = StructuredTool.from_function(
    func=_draw_figure,
    name="draw_figure",
    description=(
        "Draw a diagram yourself as SVG (crisp vector text: use it for labelled diagrams, layered "
        "architectures, maps, flowcharts, timelines; use generate_image for photos and illustrations). "
        "Give a complete <svg viewBox=...> with explicit fills; keep fonts generic (Arial, Helvetica, "
        "sans-serif). It lands in `block_id` (an image block) or as a new image block after "
        "`after_block_id` (end of document if neither). label (for example \"FIGURE 1\"), title, source "
        "and caption fill the figure's frame; figure_box gives it the grey figure panel. `block_id` may "
        "be a pending insert of yours (sug_…), which redraws it. The result comes with a PREVIEW of "
        "the figure as it sits on the page and a LABEL CHECK that lists labels that are cut off, too "
        "small, overlapping, wider than their box or crossed by a line or an arrowhead, and arrows "
        "drawn on top of each other: fix what it "
        "lists in one redraw. Keep diagrams simple: a few boxes in a row or a grid, short labels (one "
        "to three words) inside boxes with room to spare, arrows between boxes with their labels beside "
        "or above the arrow and never on it, font-size at least 1/40 of the viewBox width (a figure in "
        "one column of a two-column page prints about 330 px wide; full_width=True gives it the page "
        "width). Put explanation in the caption, not in the drawing. Arrowhead markers are kept in "
        "proportion to their lines for you. At most 5 versions of one figure per turn. comment_id: the "
        "comment this addresses."
    ),
    response_format="content_and_artifact",
)


@tool
def insert_blocks(blocks: list[dict], config: RunnableConfig, after_block_id: str | None = None,
                  note: str = "") -> str:
    """Insert several blocks in one call (drafting a section, an outline, a whole page).
    Each item: {"type": "text"|"heading"|..., "content": <same formats as propose_edit, or a
    content dict>, "level": 1-3 for headings, "layout": {"span": "full"|"column",
    "variant": "...", "fill": "accent", "dropcap": true, "pin": {...}, "repeat": "odd", "role": "cover"},
    "references": [{title, url, authors, year, publisher, key}]}. Side by side: {"type": "row", "content": {"cells":
    ["**Fast.** ...", "**Safe.** ...", "..."]}} puts 2 to 4 Markdown cells next to each other
    ("layout": {"variant": "cards"} boxes them) and {"type": "stat", "content": {"items":
    [{"value": "99.99%", "label": "delivered in 60 s"}, ...]}} is a row of number tiles; both
    are full width by default. {"type": "heading", "layout": {"variant": "band", "span":
    "full"}} is a header band and {"type": "callout", "layout": {"variant": "band"}} a
    call-to-action box. They go in order after `after_block_id` (a block, or a pending
    insert of yours) or at the end, and they stay in that order however the author accepts
    them. Write one text block per paragraph (a heading, then each paragraph as its own
    block): the author approves, comments on and versions each block separately, so a whole
    section in one block is all-or-nothing for them. Each is a suggestion; with approval
    "auto" they apply immediately and the new ids come back. While they wait, each new
    block is known by its suggestion id (sug_…), which read_blocks, propose_edit and
    after_block_id accept."""
    try:
        store = get_store()
        doc_id = _doc_id(config)
        after = after_block_id or "__end__"
        out = []
        items: list[dict] = []
        split = 0
        for item in blocks or []:
            parts = None
            if isinstance(item, dict) and item.get("type", "text") == "text" and not item.get("keep_together"):
                try:
                    parts = _should_split("text", parse_content("text", item.get("content", "")), item.get("layout"))
                except (Invalid, ValueError):
                    parts = None
            if parts:
                split += 1
                items += [{**item, "content": {"markdown": part}, **({} if j == 0 else {"references": None})}
                          for j, part in enumerate(parts)]
            else:
                items.append(item)
        for i, item in enumerate(items):
            t = item.get("type", "text")
            if t not in BLOCK_TYPES:
                out.append(f"{i}: ERROR unknown type {t!r}")
                continue
            try:
                c = parse_content(t, item.get("content", ""))
            except (Invalid, ValueError) as exc:  # one bad item must not lose the rest of the batch
                out.append(f"{i}: ERROR {t} content: {exc.args[0] if exc.args else exc}")
                continue
            if t == "heading" and item.get("level"):
                c["level"] = max(1, min(int(item["level"]), 3))
            lay = item.get("layout") or {}
            if t in _WIDE and isinstance(lay, dict) and "span" not in lay:
                lay = {**lay, "span": "full"}
            payload = {"type": t, "content": c, "after": after, "layout": lay}
            if item.get("references"):
                payload["refs"] = item["references"]
            try:
                sg = store.propose(doc_id, "insert", payload, note=note or f"Insert {t}")
            except NoChange as exc:
                out.append(f"{i}: not inserted. {exc.args[0] if exc.args else exc}")
                continue
            except (Invalid, NotFound, ValueError) as exc:
                out.append(f"{i}: ERROR {exc.args[0] if exc.args else exc}")
                continue
            new_id = sg["payload"].get("result", {}).get("block_id")
            # Keep the batch in order: each block follows the one before it, whether that
            # one is applied already or still a pending suggestion.
            after = new_id or sg["id"]
            if new_id:
                out.append(f"{i}: {t} -> {new_id}")
            else:
                out.append(f"{i}: {t} suggested ({sg['id']}, waiting for approval)")
        if split:
            out.append(f"{split} long text item(s) were split into one block per paragraph, so the author can "
                       "approve and comment on each paragraph separately.")
        if any("waiting for approval" in line for line in out):
            out.append("They will land in this order. Do not insert them again; to revise one, propose_edit "
                       "its sug_ id.")
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
    paragraph_indent_em: float | None = None, hyphens: str | None = None, reset: bool = False,
) -> str:
    """Override this document's typography and colours on top of its theme (applies
    immediately; only the values you pass change). Fonts: a family name ("Lato",
    "EB Garamond", any Google Fonts family) or a CSS font list; font_display is for display
    headings and kickers, font_label for labels, captions and figure titles. Colours: hex
    like "#002f58"; color_heading colours the headings, color_accent links, drop caps,
    rules and callout edges, color_callout/color_figure the tinted boxes. font_size_px is
    the body size (13-16 is usual). heading_case: none, uppercase or capitalize.
    text_align: left or justify. hyphens: "auto" breaks long words at syllables (the same
    way in the editor and the export; use it with justified text, to avoid rivers and
    loose lines), "manual" breaks only where the text has a soft hyphen.
    reset=True clears every override. Prefer the theme;
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
            "hyphens": hyphens,
        }
        vals = {k: (_font_stack(v) if k.startswith("font_") and isinstance(v, str) else v)
                for k, v in vals.items() if v is not None}
        if not vals:
            return "Nothing to change: pass at least one value (or reset=True)."
        doc = store.update_document(doc_id, settings={"style": vals})
        return "Style overrides now: " + json.dumps(doc["settings"]["style"]) + _cut_note(store, doc_id)
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
        return f"Matched {u['filename']}:\n- " + "\n- ".join(changes) + _cut_note(store, doc_id)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ---------------------------------------------------------------------- arithmetic
_CALC_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
             ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
             ast.USub: operator.neg, ast.UAdd: operator.pos}
_CALC_FUNCS: dict[str, Any] = {
    "sum": lambda *a: math.fsum(_flat(a)), "min": lambda *a: min(_flat(a)), "max": lambda *a: max(_flat(a)),
    "mean": lambda *a: math.fsum(_flat(a)) / len(_flat(a)), "avg": lambda *a: math.fsum(_flat(a)) / len(_flat(a)),
    "round": round, "abs": abs, "sqrt": math.sqrt, "pow": pow, "log": math.log, "log10": math.log10,
    "exp": math.exp, "floor": math.floor, "ceil": math.ceil,
}
_CALC_NAMES = {"pi": math.pi, "e": math.e}


def _flat(args: tuple) -> list:
    out: list = []
    for a in args:
        out.extend(a) if isinstance(a, (list, tuple)) else out.append(a)
    if not out:
        raise ValueError("needs at least one number")
    return out


def _calc(node: ast.AST, names: dict[str, Any]) -> Any:
    """Evaluate an arithmetic expression tree: numbers, + - * / // % **, parentheses, lists
    and a fixed set of functions. Nothing else (no attributes, no indexing, no strings)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _CALC_OPS:
        left, right = _calc(node.left, names), _calc(node.right, names)
        if isinstance(node.op, ast.Pow) and (abs(right) > 200 or abs(left) > 1e12):
            raise ValueError("exponent too large")
        return _CALC_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _CALC_OPS:
        return _CALC_OPS[type(node.op)](_calc(node.operand, names))
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_calc(x, names) for x in node.elts]
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        raise ValueError(f"unknown name {node.id!r}")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        f = _CALC_FUNCS.get(node.func.id)
        if f is None:
            raise ValueError(f"unknown function {node.func.id!r} (available: {', '.join(sorted(_CALC_FUNCS))})")
        return f(*[_calc(a, names) for a in node.args])
    raise ValueError("only numbers, + - * / // % **, parentheses and "
                     f"{', '.join(sorted(_CALC_FUNCS))} are allowed")


def _strip_thousands(expr: str) -> str:
    """1,250,000 -> 1250000, but only outside a function call or list, where a comma
    separates arguments."""
    out, stack = [], []
    for i, ch in enumerate(expr):
        if ch in "([":
            prev = expr[:i].rstrip()[-1:]
            stack.append(ch == "[" or prev.isalpha() or prev == "_")
        elif ch in ")]" and stack:
            stack.pop()
        elif ch == "," and not any(stack) and expr[i - 1:i].isdigit() and re.match(r"\d{3}(?!\d)", expr[i + 1:]):
            continue
        out.append(ch)
    return "".join(out)


def _calc_number(v: Any) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(_calc_number(x) for x in v) + "]"
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError("result is not a finite number")
        v = float(f"{v:.12g}")  # hide binary noise (0.1 + 0.2)
        return str(int(v)) if v == int(v) and abs(v) < 1e15 else repr(v)
    return str(v)


@tool
def calculate(expressions: list[str]) -> str:
    """Do arithmetic exactly instead of in your head. Use it for EVERY number you compute
    for the document: totals, sums of a column, percentages, growth, averages, unit
    conversions, each derived cell of a table. Pass all the expressions of one table in
    one call. Each expression is plain arithmetic: + - * / // % ** (or ^) and parentheses,
    with sum, min, max, mean, round, abs, sqrt, pow, log, log10, exp, floor, ceil.
    "name = expression" stores a result for later expressions in the same call, e.g.
    ["y1 = 5.2 * 1.04", "y2 = y1 * 1.04", "total = sum(5.2, y1, y2)", "round(total, 2)"].
    Thousands separators and currency signs are ignored (1,250,000 and $4.5 are numbers)."""
    names: dict[str, Any] = dict(_CALC_NAMES)
    out = []
    for raw in (expressions or [])[:60]:
        src = str(raw).strip()
        try:
            if len(src) > 600:
                raise ValueError("expression too long")
            name, expr = None, src
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)\s*(.+)$", src, re.S)
            if m:
                name, expr = m.group(1), m.group(2)
                if name in _CALC_FUNCS:
                    raise ValueError(f"{name!r} is a function name; pick another name")
            clean = _strip_thousands(expr)  # 1,250,000 -> 1250000
            clean = clean.replace("$", "").replace("€", "").replace("£", "").replace("×", "*").replace("÷", "/")
            clean = clean.replace("−", "-").replace("^", "**")
            clean = re.sub(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%(?!\s*[\d(A-Za-z_])", r"(\1/100)", clean)  # 15% -> 0.15
            value = _calc(ast.parse(clean.strip(), mode="eval").body, names)
            if name:
                names[name] = value
            out.append(f"{src} = {_calc_number(value)}" if not name else f"{name} = {_calc_number(value)}")
        except ZeroDivisionError:
            out.append(f"{src} -> ERROR: division by zero")
        except (ValueError, TypeError, SyntaxError, OverflowError, RecursionError) as exc:
            msg = "not a valid arithmetic expression" if isinstance(exc, SyntaxError) else str(exc)
            out.append(f"{src} -> ERROR: {msg}")
    return "\n".join(out) or "Nothing to calculate: pass a list of expressions."


def changes_note(doc_id: str, since: float) -> str:
    """What the author did since the agent's last turn, as one short note for the agent
    (accepted and rejected suggestions, their own edits, new and deleted blocks, new
    comments). Empty when nothing happened."""
    ch = get_store().changes_since(doc_id, since)
    lines = []
    if ch["accepted"]:
        bits = [f"{a['id']} ({a['kind']}" + (f" -> {a['block_id']}" if a["kind"] == "insert" and a["block_id"]
                                              else f" of {a['block_id']}" if a["block_id"] else "") + ")"
                for a in ch["accepted"]]
        lines.append("- accepted: " + ", ".join(bits)
                     + ". Accepted inserts are real blocks now: use their blk_ ids, not the sug_ ids.")
    if ch["rejected"]:
        bits = [f"{r['id']} ({r['kind']}" + (f" of {r['block_id']}" if r["block_id"] else "")
                + (f"; reason: {r['reason']}" if r["reason"] else "") + ")" for r in ch["rejected"]]
        lines.append("- REJECTED by the author: " + ", ".join(bits)
                     + ". Do not propose the same thing again unless asked.")
    if ch["obsolete"]:
        lines.append("- no longer applicable: " + ", ".join(
            f"{o['id']} ({o['kind']}" + (f" of {o['block_id']}" if o["block_id"] else "") + ")" for o in ch["obsolete"]))
    if ch["edited"]:
        lines.append("- edited by the author (re-read before changing them): " + ", ".join(ch["edited"]))
    if ch["added"]:
        lines.append("- added by the author: " + ", ".join(ch["added"]))
    if ch["deleted"]:
        lines.append("- deleted by the author (these ids no longer exist): " + ", ".join(ch["deleted"]))
    if ch["comments"]:
        lines.append("- new open comments: " + ", ".join(
            c["id"] + (f" on {c['block_id']}" if c["block_id"] else " (document)") for c in ch["comments"]))
    if not lines:
        return ""
    return ("[Codraft note, not written by the author] Since your last turn:\n" + "\n".join(lines)
            + "\nBlock ids you remember from earlier turns may be stale: call get_document before using them.")


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
    list_deleted_blocks,
    restore_block,
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
    calculate,
]

TOOL_NAMES = frozenset(t.name for t in CODRAFT_TOOLS)
