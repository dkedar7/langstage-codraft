"""SQLite document store: documents, blocks, per-block version trees, comments,
suggestions (the approval gate), and uploads.

One store serves both the human (HTTP API) and the agent (tools), so neither has a
private path to the document. Every mutation bumps the document's ``rev`` so the UI
can cheaply poll for changes made by the other party.

Version trees: each block has ``head`` (the version currently shown). Editing makes a
new version whose parent is ``head``. Stepping back moves ``head`` to its parent;
stepping forward moves it to a child. Editing while ``head`` already has children
starts a new branch, so nothing is ever overwritten.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from .pages import PAGE_SIZES_MM

BLOCK_TYPES = (
    "text", "heading", "image", "video", "table", "chart", "callout", "divider",
    "references", "page-break", "spacer",
)
ROLES = ("flow", "cover", "back")

DEFAULT_SETTINGS: dict[str, Any] = {
    "mode": "paged",                 # paged | pageless
    "page_size": "letter",           # a PAGE_SIZES name (letter, a4, legal, a5, slide) or custom
    "page_w_mm": None,               # custom page width (mm)
    "page_h_mm": None,               # custom page height (mm)
    "page_number_start": 1,          # number printed on the first numbered page ({page})
    "number_cover_pages": False,     # count cover and back pages when numbering
    "unnumbered_pages": [],          # page positions (1-based) that show no number and don't use one
    "orientation": "portrait",       # portrait | landscape
    "margins": {"top": 20, "right": 20, "bottom": 20, "left": 20},  # mm
    "columns": 1,                    # 1..3
    "column_gap": 8,                 # mm
    "block_gap": 4,                  # mm, the inter-block margin
    "theme": "classic",
    "min_pages": 1,                  # "Add page" raises this
    "approval": "required",          # required | auto
    # Cover and back pages: blocks with role "cover"/"back" get their own page (cover
    # enabled=False puts the cover blocks at the top of page 1 as a full-width title block).
    # align places the stack; background (colour) and image (upload id or URL) fill the
    # page; text is "light" on a dark background ("auto" decides). None = the theme's value.
    "cover": {"enabled": True, "align": None, "background": None, "image": None, "text": None},
    "back": {"align": None, "background": None, "image": None, "text": None},
    # This document's typography and colour overrides on top of its theme (themes.STYLE_KEYS).
    "style": {},
    # Running header and footer on body pages: text left/center/right, tokens {page},
    # {pages}, {title}, {year}. first_page=False skips the first body page.
    "header": {"left": "", "center": "", "right": "", "first_page": True, "rule": False},
    "footer": {"left": "", "center": "", "right": "", "first_page": True, "rule": False},
    "brief": {
        "writing_style": "",
        "objective": "",
        "platform": "",
        "audience": "",
        "language": "English",
        "output_format": "pdf",
        "notes": "",
    },
}

DEFAULT_CONTENT: dict[str, dict[str, Any]] = {
    "text": {"markdown": ""},
    "heading": {"text": "", "level": 1},
    "image": {"src": "", "alt": "", "caption": "", "fit": "contain", "sketch": "",
              "label": "", "title": "", "source": ""},
    "video": {"src": "", "poster": "", "caption": "", "loop": False, "muted": True},
    "table": {"rows": [["", ""], ["", ""]], "header": True, "caption": ""},
    "chart": {"spec": {}, "caption": ""},
    "callout": {"markdown": "", "tone": "info"},
    "divider": {},
    "references": {"title": "References", "style": "numbered"},
    "page-break": {},
    "spacer": {"label": ""},   # an empty reserved area (pin it so text flows around it)
}

DEFAULT_LAYOUT: dict[str, Any] = {
    "span": "column",    # column | full
    "height": None,      # fixed height in px, or None for auto
    "role": "flow",      # flow | cover | back
    "pin": None,         # {"page", "top", "column", "span", "height"} or page-anchored {"anchor": "page", "page", "x", "y", "w", "h"}
    "repeat": None,      # None | "all" | "odd" | "even" | [page numbers]
    "variant": None,     # a typographic style, see BLOCK_VARIANTS
    "dropcap": False,    # text blocks: a large initial letter
}

# Typographic styles a block can wear (the theme supplies fonts and colours).
BLOCK_VARIANTS: dict[str, tuple[str, ...]] = {
    "text": ("lead", "pullquote", "kicker", "byline", "small", "caption", "footer", "bio"),
    "heading": ("display", "section", "rule", "label"),
    "image": ("figure-box",),
    "chart": ("figure-box",),
    "table": ("figure-box",),
    "callout": ("figure-box", "dark"),
    "references": ("hanging",),
}

# What each variant looks like, for the Block panel and for an agent choosing one. The heading
# variants differ in whether they follow the document's heading settings, which is the thing
# an agent most needs to know before it says a heading's look has changed.
VARIANT_NOTES: dict[str, str] = {
    "lead": "an opening paragraph, set a little larger",
    "pullquote": "a pulled quote: Titles font, capitals, right-aligned",
    "kicker": "a large line in the Titles font, above or below a title",
    "byline": "author and date: small spaced capitals in the Titles font",
    "small": "fine print in the Labels font",
    "caption": "a bold caption line in the Labels font",
    "footer": "a closing line: tiny spaced capitals",
    "bio": "an italic author note",
    "display": "a title at a fixed large size in the Titles font (font_display, display_weight); "
               "it ignores heading_case, heading_weight and heading_scale",
    "section": "a compact section heading: by default small, bold capitals in the Labels font. "
               "A heading_case, heading_weight, heading_scale or font_heading the document sets "
               "itself replaces that default",
    "rule": "like section, with a rule above it; it follows the document's own heading "
            "settings in the same way",
    "label": "a small spaced-capitals label in the Labels font (font_label); it ignores "
             "heading_case, heading_scale and font_heading",
    "figure-box": "a tinted box around the figure, table or callout",
    "dark": "a dark, centred callout",
    "hanging": "an unnumbered list with hanging indents",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, settings TEXT NOT NULL,
  rev INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS blocks (
  id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, ord REAL NOT NULL, type TEXT NOT NULL,
  layout TEXT NOT NULL, head TEXT, created_at REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS blocks_doc ON blocks(doc_id, deleted, ord);
CREATE TABLE IF NOT EXISTS versions (
  id TEXT PRIMARY KEY, block_id TEXT NOT NULL, parent_id TEXT, content TEXT NOT NULL,
  refs TEXT NOT NULL, author TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS versions_block ON versions(block_id, created_at);
CREATE TABLE IF NOT EXISTS comments (
  id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, block_id TEXT, body TEXT NOT NULL,
  author TEXT NOT NULL, for_agent INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL,
  replies TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL, resolved_at REAL
);
CREATE TABLE IF NOT EXISTS suggestions (
  id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, block_id TEXT, kind TEXT NOT NULL,
  payload TEXT NOT NULL, status TEXT NOT NULL, comment_id TEXT, note TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL, created_at REAL NOT NULL, decided_at REAL
);
CREATE TABLE IF NOT EXISTS uploads (
  id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, filename TEXT NOT NULL, purpose TEXT NOT NULL,
  mime TEXT NOT NULL, path TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS themes (
  name TEXT PRIMARY KEY, data TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
"""

UPLOAD_PURPOSES = ("reference", "template", "style", "asset")
_HF_SLOTS = ("left", "center", "right")


class NotFound(KeyError):
    """A document, block, comment, suggestion or upload id that doesn't exist."""


class Invalid(ValueError):
    """A request the store refuses (bad type, bad layout, wrong state)."""


class NoChange(Invalid):
    """A suggestion that would change nothing, or that repeats one still waiting for the
    author. Nothing is recorded; the message says which, so an agent stops re-proposing."""


# The reject reason of a suggestion its own author took back (not a decision by the human).
WITHDRAWN = "withdrawn by the agent"

# page_size values a document can store: the named sizes every renderer knows, or custom.
PAGE_SIZES = (*PAGE_SIZES_MM, "custom")


def _number(name: str, v: Any, lo: float, hi: float) -> None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise Invalid(f"settings.{name} must be a number from {lo:g} to {hi:g}")


def _integer(name: str, v: Any, lo: int, hi: int) -> None:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise Invalid(f"settings.{name} must be a whole number from {lo} to {hi}")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _check_content(content: Any) -> None:
    """Block content is an object of fields, e.g. {"markdown": "..."} (gh #28)."""
    if content is not None and not isinstance(content, dict):
        raise Invalid(
            f"content must be an object, e.g. {{\"markdown\": \"...\"}}; got {type(content).__name__}"
        )


# The content fields each block type reads with a fixed type (gh #34). Other keys pass through.
_TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "text": ("markdown",),
    "callout": ("markdown", "tone"),
    "heading": ("text",),
    "image": ("src", "alt", "caption", "fit", "sketch", "label", "title", "source"),
    "video": ("src", "poster", "caption"),
    "table": ("caption",),
    "chart": ("caption",),
    "references": ("title", "style"),
    "spacer": ("label",),
}
_BOOL_FIELDS: dict[str, tuple[str, ...]] = {"video": ("loop", "muted"), "table": ("header",)}


def _check_fields(block_type: str, content: dict | None) -> dict | None:
    """Refuse a content field of the wrong type, naming it (gh #34). A list of strings for
    ``markdown`` is joined into paragraphs. Returns the content to store."""
    if not content:
        return content
    out = dict(content)
    if isinstance(out.get("markdown"), list) and all(isinstance(p, str) for p in out["markdown"]):
        out["markdown"] = "\n\n".join(out["markdown"])
    for k in _TEXT_FIELDS.get(block_type, ()):
        if out.get(k) is not None and not isinstance(out[k], str):
            raise Invalid(f"content.{k} of a {block_type} block must be text; got {type(out[k]).__name__}")
    for k in _BOOL_FIELDS.get(block_type, ()):
        if out.get(k) is not None and not isinstance(out[k], bool):
            raise Invalid(f"content.{k} of a {block_type} block must be true or false")
    if block_type == "heading" and out.get("level") is not None:
        lv = out["level"]
        if isinstance(lv, bool) or not isinstance(lv, int) or not 1 <= lv <= 6:
            raise Invalid("content.level of a heading block must be a whole number from 1 to 6 (levels above 3 render as 3)")
    if block_type == "table" and out.get("rows") is not None:
        rows = out["rows"]
        if not isinstance(rows, list) or not all(
            isinstance(r, list) and all(c is None or isinstance(c, (str, int, float)) for c in r)
            for r in rows
        ):
            raise Invalid("content.rows of a table block must be a list of rows, each a list of cells")
    if block_type == "chart" and out.get("spec") is not None and not isinstance(out["spec"], dict):
        raise Invalid("content.spec of a chart block must be an object (a Vega-Lite spec)")
    return out


def _as_text(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "\n\n".join(_as_text(p) for p in v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return ""


def repair_content(block_type: str, content: Any) -> dict:
    """Content as the renderers expect it: a stored wrong-typed text field (written before
    gh #34) is read as text, so one bad block can't blank the editor or hang an export."""
    if not isinstance(content, dict):
        return {}
    bad = [k for k in _TEXT_FIELDS.get(block_type, ()) if content.get(k) is not None
           and not isinstance(content[k], str)]
    if block_type == "heading" and content.get("level") is not None and (
            isinstance(content["level"], bool) or not isinstance(content["level"], int)):
        bad.append("level")
    if not bad:
        return content
    out = dict(content)
    for k in bad:
        out[k] = 1 if k == "level" else _as_text(out[k])
    return out


def _merge(base: dict, overlay: dict) -> dict:
    out = deepcopy(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def plain_text(block_type: str, content: dict) -> str:
    """Best-effort plain text of a block, for search, excerpts and word counts."""
    if block_type in ("text", "callout"):
        md = content.get("markdown", "")
        md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)
        md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
        return re.sub(r"[#>*_`~|-]+", " ", md).strip()
    if block_type == "heading":
        return content.get("text", "")
    if block_type == "table":
        cells = [" ".join(map(str, r)) for r in content.get("rows", [])]
        return " ".join([content.get("caption", "")] + cells).strip()
    if block_type in ("image", "chart", "video"):
        return " ".join(filter(None, [content.get("alt", ""), content.get("caption", "")]))
    if block_type == "references":
        return content.get("title", "")
    return ""


def check_variant(block_type: str, layout: dict) -> None:
    v = layout.get("variant")
    if v and v not in BLOCK_VARIANTS.get(block_type, ()):
        raise Invalid(f"variant {v!r} doesn't apply to a {block_type} block; "
                      f"options: {', '.join(BLOCK_VARIANTS.get(block_type, ())) or 'none'}")


def check_block(block_type: str, content: Any, layout: Any, columns: int = 3) -> tuple[dict | None, dict]:
    """The checks a new block gets: its type, content and layout. Returns the content and
    layout to store, or raises :class:`Invalid`. Deployer templates run the same checks
    when they load, so ``check`` can't pass a template that ``new`` refuses (gh #41)."""
    if block_type not in BLOCK_TYPES:
        raise Invalid(f"unknown block type {block_type!r}; expected one of {', '.join(BLOCK_TYPES)}")
    _check_content(content)
    content = _check_fields(block_type, content)
    if layout is not None and not isinstance(layout, dict):
        raise Invalid(f"layout must be an object; got {type(layout).__name__}")
    try:
        lay = validate_layout(layout or {}, columns)
    except (TypeError, ValueError, AttributeError) as exc:  # e.g. a pin page that isn't a number
        raise Invalid(f"invalid layout: {exc}") from None
    check_variant(block_type, lay)
    return content, lay


def validate_layout(layout: dict, columns: int = 3) -> dict:
    out = _merge(DEFAULT_LAYOUT, layout or {})
    if out["span"] not in ("column", "full"):
        raise Invalid("layout.span must be 'column' or 'full'")
    if out["role"] not in ROLES:
        raise Invalid(f"layout.role must be one of {ROLES}")
    if out["height"] is not None:
        out["height"] = max(16, int(out["height"]))
    v = out.get("variant")
    if v in ("", "none", "default"):
        out["variant"] = None
    out["dropcap"] = bool(out.get("dropcap"))
    pin = out.get("pin")
    if pin and pin.get("anchor") == "page":
        # Page-anchored: px from the page's top-left corner (may bleed past the margins).
        pin = {"anchor": "page", "page": 1, "x": 0, "y": 0, "w": 200, "h": 100, **pin}
        pin["page"] = max(1, int(pin["page"]))
        for k in ("x", "y"):
            pin[k] = int(pin[k])
        pin["w"], pin["h"] = max(8, int(pin["w"])), max(8, int(pin["h"]))
        out["pin"] = {k: pin[k] for k in ("anchor", "page", "x", "y", "w", "h")}
        pin = None
    if pin:
        pin = {"page": 1, "top": 0, "column": 0, "span": 1, "height": None, **pin}
        pin["page"] = max(1, int(pin["page"]))
        pin["top"] = max(0, int(pin["top"]))
        pin["column"] = max(0, min(int(pin["column"]), columns - 1))
        pin["span"] = max(1, min(int(pin["span"]), columns - pin["column"]))
        out["pin"] = pin
    rep = out.get("repeat")
    if rep not in (None, "all", "odd", "even") and not (
        isinstance(rep, list) and all(isinstance(p, int) and p >= 1 for p in rep)
    ):
        raise Invalid("layout.repeat must be null, 'all', 'odd', 'even' or a list of page numbers")
    if rep and not out.get("pin"):
        raise Invalid("only a pinned block can repeat across pages")
    return out


class _TxLock:
    """The store's re-entrant lock, and its transaction boundary.

    Every store method runs under it and commits its own writes. If the outermost method
    raises instead, whatever it wrote is rolled back, so a refused or failed request can
    never leave a partial row behind for the next commit to keep (gh #28).
    """

    def __init__(self, store: Store):
        self._rlock = threading.RLock()
        self._store = store
        self._depth = 0

    def __enter__(self) -> _TxLock:
        self._rlock.acquire()
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._depth -= 1
            if self._depth == 0 and exc_type is not None:
                try:
                    if self._store._db.in_transaction:
                        self._store._db.rollback()
                except sqlite3.ProgrammingError:  # closed: nothing left to undo
                    pass
        finally:
            self._rlock.release()


class Store:
    """Thread-safe SQLite store. One instance per server process."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.uploads_dir = self.path.parent / "uploads"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self._lock = _TxLock(self)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        cols = {r["name"] for r in self._db.execute("PRAGMA table_info(uploads)").fetchall()}
        if "meta" not in cols:  # 0.5: rendered pages and the extracted style of an upload
            self._db.execute("ALTER TABLE uploads ADD COLUMN meta TEXT NOT NULL DEFAULT '{}'")
        cols = {r["name"] for r in self._db.execute("PRAGMA table_info(blocks)").fetchall()}
        if "deleted_at" not in cols:  # when a block was deleted: to list and restore it, and for the
            # agent's "what changed" note
            self._db.execute("ALTER TABLE blocks ADD COLUMN deleted_at REAL")
        # Before 0.5.16, deleting a block left its pending suggestions pending forever (gh #39).
        self._db.execute(
            "UPDATE suggestions SET status = 'obsolete', decided_at = ? WHERE status = 'pending'"
            " AND block_id IN (SELECT id FROM blocks WHERE deleted = 1)", (time.time(),))
        self._db.commit()
        # Last client-computed layout per document (block -> pages), reported by the UI
        # after it paginates, so the agent's outline can say which page a block is on.
        self._layout_reports: dict[str, dict] = {}
        # Per document: where the agent's last turn ended, so its next turn can be told what
        # the author did in between (middleware). In memory: a restart just skips one note.
        self._agent_turns: dict[str, dict] = {}

    # ---------------------------------------------------------------- helpers
    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self._db.execute(sql, args).fetchall()

    def _one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        return self._db.execute(sql, args).fetchone()

    def _bump(self, doc_id: str) -> None:
        self._db.execute(
            "UPDATE documents SET rev = rev + 1, updated_at = ? WHERE id = ?", (time.time(), doc_id)
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -------------------------------------------------------------- documents
    def create_document(
        self, title: str = "Untitled document", settings: dict | None = None,
        blocks: list[dict] | None = None,
    ) -> dict:
        with self._lock:
            doc_id = _id("doc")
            now = time.time()
            merged = _merge(DEFAULT_SETTINGS, settings or {})
            self._check_settings(merged)
            self._db.execute(
                "INSERT INTO documents (id, title, settings, rev, created_at, updated_at)"
                " VALUES (?, ?, ?, 0, ?, ?)",
                (doc_id, title or "Untitled document", json.dumps(merged), now, now),
            )
            for i, b in enumerate(blocks or []):
                self._insert_block_row(
                    doc_id, b.get("type", "text"), b.get("content"), b.get("layout"),
                    ord_=float(i + 1), refs=b.get("refs"), author="template", columns=merged["columns"],
                )
            self._db.commit()
            return self.get_document(doc_id)

    @staticmethod
    def _check_settings(s: dict, changed: set[str] | None = None) -> None:
        """Refuse settings the renderers would ignore or choke on (gh #30). ``changed`` is
        the keys a PATCH sets: the checks added in 0.5.12 only look at those, so a document
        saved with an odd value by an older version can still be edited. None = all keys."""
        from . import themes

        def new(key: str) -> bool:
            return changed is None or key in changed

        for page in ("cover", "back", "header", "footer"):
            if s.get(page) is not None and not isinstance(s[page], dict):
                raise Invalid(f"settings.{page} must be an object")
        if new("page_size"):
            ps = s.get("page_size")
            if isinstance(ps, str):
                s["page_size"] = ps = ps.strip().lower()
            if ps not in PAGE_SIZES:
                raise Invalid(f"settings.page_size must be one of {', '.join(PAGE_SIZES)} (got {ps!r}); "
                              "for any other size use custom with page_w_mm and page_h_mm")
        for k in ("page_w_mm", "page_h_mm"):
            if new(k) and changed is not None and s.get(k) is not None and s.get("page_size") != "custom":
                raise Invalid(f"settings.{k} only applies with page_size 'custom'")
        if new("margins"):
            m = s.get("margins")
            if not isinstance(m, dict) or set(m) != {"top", "right", "bottom", "left"}:
                raise Invalid("settings.margins must be {top, right, bottom, left} in mm")
            for side, v in m.items():
                _number(f"margins.{side}", v, 0, 80)
        for k in ("block_gap", "column_gap"):
            if new(k):
                _number(k, s.get(k), 0, 30)
        if new("page_number_start"):
            _integer("page_number_start", s.get("page_number_start"), 0, 100000)
        if new("min_pages"):
            _integer("min_pages", s.get("min_pages"), 1, 500)
        if new("number_cover_pages") and not isinstance(s.get("number_cover_pages"), bool):
            raise Invalid("settings.number_cover_pages must be true or false")
        if new("brief"):
            b = s.get("brief")
            if not isinstance(b, dict) or not all(isinstance(v, str) and len(v) <= 5000 for v in b.values()):
                raise Invalid("settings.brief must be an object of text fields (5000 characters at most)")
        if not isinstance(s.get("style") or {}, dict):
            raise Invalid("settings.style must be an object of style values (or null to reset)")
        try:
            s["style"] = themes.clean_values(s.get("style") or {}, themes.STYLE_KEYS)
        except ValueError as exc:
            raise Invalid(f"settings.style: {exc}") from None
        for page in ("cover", "back"):
            c = dict(s.get(page) or {})
            if page == "cover":
                c["enabled"] = bool(c.get("enabled", True))
            if c.get("align") not in (None, "top", "center", "bottom"):
                raise Invalid(f"settings.{page}.align must be top, center or bottom")
            if c.get("text") not in (None, "auto", "light", "dark"):
                raise Invalid(f"settings.{page}.text must be auto, light or dark")
            if c.get("background") not in (None, "") and not themes.is_color(c["background"]):
                raise Invalid(f"settings.{page}.background must be a colour like #002f58")
            if c.get("image") not in (None, "") and not themes.is_image_ref(c["image"]):
                raise Invalid(f"settings.{page}.image must be an upload id or an image URL")
            for k in ("background", "image"):
                c[k] = c.get(k) or None
            s[page] = c
        for part in ("header", "footer"):
            h = dict(s.get(part) or {})
            for slot in _HF_SLOTS:
                v = h.get(slot) or ""
                if not isinstance(v, str) or len(v) > 300:
                    raise Invalid(f"settings.{part}.{slot} must be text (300 characters at most)")
                h[slot] = v
            h["first_page"] = bool(h.get("first_page", True))
            h["rule"] = bool(h.get("rule", False))
            s[part] = h
        if s["mode"] not in ("paged", "pageless"):
            raise Invalid("settings.mode must be 'paged' or 'pageless'")
        if int(s["columns"]) not in (1, 2, 3):
            raise Invalid("settings.columns must be 1, 2 or 3")
        if s["approval"] not in ("required", "auto"):
            raise Invalid("settings.approval must be 'required' or 'auto'")
        if s["orientation"] not in ("portrait", "landscape"):
            raise Invalid("settings.orientation must be 'portrait' or 'landscape'")
        up = s.get("unnumbered_pages") or []
        if not isinstance(up, list) or not all(isinstance(n, int) and n >= 1 for n in up):
            raise Invalid("settings.unnumbered_pages must be a list of page positions (1, 2, ...)")
        if s.get("page_size") == "custom":
            for k in ("page_w_mm", "page_h_mm"):
                v = s.get(k)
                if not isinstance(v, (int, float)) or not 50 <= float(v) <= 1200:
                    raise Invalid(f"settings.{k} must be 50-1200 mm for a custom page size")

    def list_documents(self) -> list[dict]:
        with self._lock:
            rows = self._q(
                "SELECT d.id, d.title, d.updated_at, d.created_at, d.settings,"
                " (SELECT COUNT(*) FROM blocks b WHERE b.doc_id = d.id AND b.deleted = 0) AS n_blocks"
                " FROM documents d WHERE d.deleted = 0 ORDER BY d.updated_at DESC"
            )
            return [
                {"id": r["id"], "title": r["title"], "updated_at": r["updated_at"],
                 "created_at": r["created_at"], "n_blocks": r["n_blocks"],
                 "mode": json.loads(r["settings"]).get("mode")}
                for r in rows
            ]

    def get_document(self, doc_id: str) -> dict:
        with self._lock:
            r = self._one("SELECT * FROM documents WHERE id = ? AND deleted = 0", (doc_id,))
            if r is None:
                raise NotFound(f"document {doc_id} not found")
            return {
                "id": r["id"], "title": r["title"],
                # Defaults underneath, so documents from older versions get new settings.
                "settings": _merge(DEFAULT_SETTINGS, json.loads(r["settings"])),
                "rev": r["rev"], "created_at": r["created_at"], "updated_at": r["updated_at"],
            }

    def rev(self, doc_id: str) -> int:
        return self.get_document(doc_id)["rev"]

    def update_document(self, doc_id: str, *, title: str | None = None, settings: dict | None = None) -> dict:
        with self._lock:
            doc = self.get_document(doc_id)
            if title is not None:
                self._db.execute("UPDATE documents SET title = ? WHERE id = ?", (title, doc_id))
            if settings:
                if "style" in settings and settings["style"] is None:  # reset to the theme
                    settings = {**settings, "style": {}}
                    doc["settings"]["style"] = {}
                merged = _merge(doc["settings"], settings)
                try:
                    merged["columns"] = int(merged["columns"])
                except (TypeError, ValueError):
                    raise Invalid("settings.columns must be 1, 2 or 3") from None
                self._check_settings(merged, changed=set(settings))
                self._db.execute(
                    "UPDATE documents SET settings = ? WHERE id = ?", (json.dumps(merged), doc_id)
                )
            self._bump(doc_id)
            self._db.commit()
            return self.get_document(doc_id)

    def delete_document(self, doc_id: str) -> None:
        with self._lock:
            self.get_document(doc_id)
            self._db.execute("UPDATE documents SET deleted = 1 WHERE id = ?", (doc_id,))
            self._db.commit()

    def duplicate_document(self, doc_id: str) -> dict:
        with self._lock:
            doc = self.get_document(doc_id)
            blocks = [
                {"type": b["type"], "content": b["content"], "layout": b["layout"], "refs": b["refs"]}
                for b in self.list_blocks(doc_id)
            ]
            return self.create_document(f"{doc['title']} (copy)", doc["settings"], blocks)

    def full(self, doc_id: str) -> dict:
        """Everything the editor needs in one payload."""
        with self._lock:
            doc = self.get_document(doc_id)
            doc["blocks"] = self.list_blocks(doc_id)
            doc["comments"] = self.list_comments(doc_id, include_resolved=True)
            doc["suggestions"] = self.list_suggestions(doc_id)
            doc["uploads"] = self.list_uploads(doc_id)
            return doc

    # ----------------------------------------------------------------- blocks
    def _insert_block_row(
        self, doc_id: str, block_type: str, content: dict | None, layout: dict | None,
        *, ord_: float, refs: list | None, author: str, columns: int = 3, note: str = "",
    ) -> str:
        content, lay = check_block(block_type, content, layout, columns)
        block_id = _id("blk")
        now = time.time()
        self._db.execute(
            "INSERT INTO blocks (id, doc_id, ord, type, layout, head, created_at) VALUES (?,?,?,?,?,NULL,?)",
            (block_id, doc_id, ord_, block_type, json.dumps(lay), now),
        )
        self._new_version(block_id, None, _merge(DEFAULT_CONTENT[block_type], content or {}),
                          refs or [], author, note or "created")
        return block_id

    def _new_version(self, block_id: str, parent: str | None, content: dict, refs: list,
                     author: str, note: str) -> str:
        vid = _id("ver")
        self._db.execute(
            "INSERT INTO versions (id, block_id, parent_id, content, refs, author, note, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (vid, block_id, parent, json.dumps(content), json.dumps(_clean_refs(refs)), author, note,
             time.time()),
        )
        self._db.execute("UPDATE blocks SET head = ? WHERE id = ?", (vid, block_id))
        return vid

    def _block_row(self, block_id: str) -> sqlite3.Row:
        r = self._one("SELECT * FROM blocks WHERE id = ? AND deleted = 0", (block_id,))
        if r is None:
            raise NotFound(f"block {block_id} not found")
        return r

    def _block_dict(self, r: sqlite3.Row) -> dict:
        v = self._one("SELECT * FROM versions WHERE id = ?", (r["head"],))
        n = self._one("SELECT COUNT(*) AS n FROM versions WHERE block_id = ?", (r["id"],))["n"]
        return {
            "id": r["id"], "doc_id": r["doc_id"], "type": r["type"], "ord": r["ord"],
            "layout": json.loads(r["layout"]),
            "content": repair_content(r["type"], json.loads(v["content"])) if v else {},
            "refs": json.loads(v["refs"]) if v else [],
            "head": r["head"], "n_versions": n,
            "author": v["author"] if v else "", "updated_at": v["created_at"] if v else r["created_at"],
        }

    def list_blocks(self, doc_id: str) -> list[dict]:
        """Blocks in reading order: cover blocks, then flow, then back-page blocks."""
        with self._lock:
            self.get_document(doc_id)
            rows = self._q("SELECT * FROM blocks WHERE doc_id = ? AND deleted = 0 ORDER BY ord", (doc_id,))
            blocks = [self._block_dict(r) for r in rows]
            rank = {"cover": 0, "flow": 1, "back": 2}
            return sorted(blocks, key=lambda b: (rank.get(b["layout"].get("role", "flow"), 1), b["ord"]))

    def get_block(self, block_id: str) -> dict:
        with self._lock:
            return self._block_dict(self._block_row(block_id))

    def block_in(self, doc_id: str, block_id: str) -> dict:
        """The block, refusing one that belongs to another document (gh #25)."""
        b = self.get_block(block_id)
        if b["doc_id"] != doc_id:
            raise Invalid(f"block {block_id} is not in document {doc_id}")
        return b

    def _ord_after(self, doc_id: str, after_id: str | None) -> float:
        rows = self._q("SELECT id, ord FROM blocks WHERE doc_id = ? AND deleted = 0 ORDER BY ord", (doc_id,))
        if not rows:
            return 1.0
        if after_id is None:  # insert at the very start
            return rows[0]["ord"] - 1.0
        if after_id == "__end__":
            return rows[-1]["ord"] + 1.0
        for i, r in enumerate(rows):
            if r["id"] == after_id:
                nxt = rows[i + 1]["ord"] if i + 1 < len(rows) else r["ord"] + 2.0
                return (r["ord"] + nxt) / 2.0
        raise NotFound(f"block {after_id} not found in document {doc_id}")

    def insert_block(
        self, doc_id: str, block_type: str, content: dict | None = None, *,
        after: str | None = "__end__", layout: dict | None = None, refs: list | None = None,
        author: str = "user", note: str = "",
    ) -> dict:
        with self._lock:
            doc = self.get_document(doc_id)
            ord_ = self._ord_after(doc_id, after)
            layout = self._inherit_role(after, layout)
            bid = self._insert_block_row(
                doc_id, block_type, content, layout, ord_=ord_, refs=refs, author=author,
                columns=doc["settings"]["columns"], note=note,
            )
            self._bump(doc_id)
            self._db.commit()
            return self.get_block(bid)

    def insert_blocks(self, doc_id: str, blocks: list[dict], *, author: str = "user",
                      note: str = "") -> list[str]:
        """Append many blocks (``{type, content, layout, refs}``) in one transaction: all
        of them or, if one is refused, none. Used by import. Returns the new block ids."""
        with self._lock:
            doc = self.get_document(doc_id)
            last = self._one("SELECT MAX(ord) AS m FROM blocks WHERE doc_id = ? AND deleted = 0", (doc_id,))
            base = last["m"] if last and last["m"] is not None else 0.0
            ids = [
                self._insert_block_row(
                    doc_id, b.get("type", "text"), b.get("content"), b.get("layout"),
                    ord_=base + i + 1, refs=b.get("refs"), author=author,
                    columns=doc["settings"]["columns"], note=note,
                )
                for i, b in enumerate(blocks)
            ]
            self._bump(doc_id)
            self._db.commit()
            return ids

    def _inherit_role(self, after: str | None, layout: dict | None) -> dict | None:
        """A block added right after a cover (or back-page) block, with no role of its own,
        joins that page instead of landing in the body."""
        if (layout or {}).get("role") or after in (None, "__end__"):
            return layout
        r = self._one("SELECT layout FROM blocks WHERE id = ? AND deleted = 0", (after,))
        role = json.loads(r["layout"]).get("role") if r else None
        if role in ("cover", "back"):
            return {**(layout or {}), "role": role}
        return layout

    def edit_block(
        self, block_id: str, *, content: dict | None = None, refs: list | None = None,
        author: str = "user", note: str = "", base: str | None = None,
    ) -> dict:
        """Write a new version (content and/or refs) as a child of the current head.

        ``base`` is the version the editor started from. If someone else changed the block
        meanwhile (head != base), the edit becomes a new *branch* off ``base`` and is
        checked out; the other person's version stays in the tree. Nothing is lost, and the
        result carries ``conflict: True`` so the UI can say so.
        """
        _check_content(content)
        with self._lock:
            b = self.get_block(block_id)
            content = _check_fields(b["type"], content)
            if base and base != b["head"]:
                v = self._one("SELECT * FROM versions WHERE id = ? AND block_id = ?", (base, block_id))
                if v is not None:
                    base_content = json.loads(v["content"])
                    new_content = _merge(base_content, content) if content is not None else base_content
                    if b["type"] == "table" and content and "rows" in content:
                        new_content["rows"] = content["rows"]
                    if b["type"] == "chart" and content and "spec" in content:
                        new_content["spec"] = content["spec"]
                    new_refs = refs if refs is not None else json.loads(v["refs"])
                    self._new_version(block_id, base, new_content, new_refs, author,
                                      (note or "edited") + " (concurrent edit: new branch)")
                    self._bump(b["doc_id"])
                    self._db.commit()
                    return {**self.get_block(block_id), "conflict": True}
            new_content = _merge(b["content"], content) if content is not None else b["content"]
            if b["type"] == "table" and content and "rows" in content:
                new_content["rows"] = content["rows"]  # lists replace, never merge
            if b["type"] == "chart" and content and "spec" in content:
                new_content["spec"] = content["spec"]
            new_refs = refs if refs is not None else b["refs"]
            if new_content == b["content"] and _clean_refs(new_refs) == b["refs"]:
                return b
            self._new_version(block_id, b["head"], new_content, new_refs, author, note)
            self._bump(b["doc_id"])
            self._db.commit()
            return self.get_block(block_id)

    def set_layout(self, block_id: str, layout: dict) -> dict:
        """Layout is styling, not content: it is applied in place, not versioned."""
        with self._lock:
            b = self.get_block(block_id)
            cols = self.get_document(b["doc_id"])["settings"]["columns"]
            merged = _merge(b["layout"], layout)
            if "pin" in layout and layout["pin"] is None:  # unpinning also stops repeating
                merged["pin"] = None
                merged["repeat"] = None
            lay = validate_layout(merged, cols)
            check_variant(b["type"], lay)
            self._db.execute("UPDATE blocks SET layout = ? WHERE id = ?", (json.dumps(lay), block_id))
            self._bump(b["doc_id"])
            self._db.commit()
            return self.get_block(block_id)

    def move_block(self, block_id: str, after: str | None) -> dict:
        with self._lock:
            b = self.get_block(block_id)
            if after == block_id:
                return b
            self._db.execute("UPDATE blocks SET ord = -1e18 WHERE id = ?", (block_id,))  # out of the way
            self._db.execute("UPDATE blocks SET deleted = 2 WHERE id = ?", (block_id,))
            try:
                ord_ = self._ord_after(b["doc_id"], after)
            finally:
                self._db.execute("UPDATE blocks SET deleted = 0 WHERE id = ?", (block_id,))
            self._db.execute("UPDATE blocks SET ord = ? WHERE id = ?", (ord_, block_id))
            self._bump(b["doc_id"])
            self._db.commit()
            return self.get_block(block_id)

    def delete_block(self, block_id: str, *, keep: str | None = None) -> None:
        """Delete a block. Its pending suggestions become ``obsolete`` (there is nothing left
        to apply them to), and pending inserts or moves anchored after it move up to follow
        the block before it (gh #39). ``keep`` is a suggestion being accepted right now."""
        with self._lock:
            b = self.get_block(block_id)
            ids = [r["id"] for r in self._q(
                "SELECT id FROM blocks WHERE doc_id = ? AND deleted = 0 ORDER BY ord", (b["doc_id"],))]
            prev = ids[ids.index(block_id) - 1] if ids.index(block_id) > 0 else None
            now = time.time()
            # "Recently deleted" lists newest first: two deletes in the same clock tick (a
            # coarse timer, or Accept all) still get distinct, increasing times.
            last = self._one("SELECT MAX(deleted_at) AS m FROM blocks WHERE doc_id = ?", (b["doc_id"],))["m"]
            when = max(now, (last or 0) + 1e-3)
            self._db.execute("UPDATE blocks SET deleted = 1, deleted_at = ? WHERE id = ?", (when, block_id))
            for r in self._q("SELECT id, block_id, kind, payload FROM suggestions"
                             " WHERE doc_id = ? AND status = 'pending' AND id IS NOT ?", (b["doc_id"], keep)):
                p = json.loads(r["payload"])
                if r["block_id"] == block_id:
                    p = {**p, "obsolete_reason": "block deleted"} if isinstance(p, dict) else p
                    self._db.execute("UPDATE suggestions SET status = 'obsolete', decided_at = ?, payload = ?"
                                     " WHERE id = ?", (now, json.dumps(p), r["id"]))
                elif r["kind"] in ("insert", "move") and isinstance(p, dict) and p.get("after") == block_id:
                    self._db.execute("UPDATE suggestions SET payload = ? WHERE id = ?",
                                     (json.dumps({**p, "after": prev}), r["id"]))
            self._bump(b["doc_id"])
            self._db.commit()

    def deleted_blocks(self, doc_id: str, *, limit: int = 50) -> list[dict]:
        """A document's deleted blocks, most recently deleted first. A deleted block keeps its
        content, layout and version history, so it can come back with :meth:`restore_block`."""
        with self._lock:
            self.get_document(doc_id)
            rows = self._q(
                "SELECT * FROM blocks WHERE doc_id = ? AND deleted = 1"
                " ORDER BY COALESCE(deleted_at, created_at) DESC, ord LIMIT ?", (doc_id, max(1, limit)))
            out = []
            for r in rows:
                b = self._block_dict(r)
                out.append({"id": b["id"], "type": b["type"], "deleted_at": r["deleted_at"],
                            "preview": " ".join(plain_text(b["type"], b["content"]).split())[:160],
                            "n_versions": b["n_versions"], "author": b["author"]})
            return out

    def restore_block(self, block_id: str) -> dict:
        """Bring a deleted block back where it was, with its content, layout and history.
        Suggestions that became obsolete when it was deleted stay obsolete."""
        with self._lock:
            r = self._one("SELECT * FROM blocks WHERE id = ?", (block_id,))
            if r is None:
                raise NotFound(f"block {block_id} not found")
            if r["deleted"] != 1:
                raise Invalid(f"block {block_id} is not deleted")
            doc = self.get_document(r["doc_id"])  # NotFound if the document itself is gone
            # The page layout may have changed since (fewer columns): keep the layout if it is
            # still valid, else restore the block unpinned so it is at least on the page.
            layout = json.loads(r["layout"])
            try:
                layout = validate_layout(layout, doc["settings"]["columns"])
            except Invalid:
                layout = validate_layout({**layout, "pin": None, "repeat": None}, doc["settings"]["columns"])
            self._db.execute("UPDATE blocks SET deleted = 0, deleted_at = NULL, layout = ? WHERE id = ?",
                             (json.dumps(layout), block_id))
            self._bump(r["doc_id"])
            self._db.commit()
            return self.get_block(block_id)

    def duplicate_block(self, block_id: str, *, author: str = "user") -> dict:
        with self._lock:
            b = self.get_block(block_id)
            lay = deepcopy(b["layout"])
            if lay.get("pin"):
                lay["pin"]["top"] = lay["pin"]["top"] + 24
            return self.insert_block(
                b["doc_id"], b["type"], b["content"], after=block_id, layout=lay,
                refs=b["refs"], author=author, note=f"duplicated from {block_id}",
            )

    # --------------------------------------------------------------- versions
    def versions(self, block_id: str) -> dict:
        """The whole version tree of a block, plus the current head."""
        with self._lock:
            b = self.get_block(block_id)
            rows = self._q("SELECT * FROM versions WHERE block_id = ? ORDER BY created_at", (block_id,))
            items = [
                {"id": r["id"], "parent_id": r["parent_id"],
                 "content": repair_content(b["type"], json.loads(r["content"])),
                 "refs": json.loads(r["refs"]), "author": r["author"], "note": r["note"],
                 "created_at": r["created_at"]}
                for r in rows
            ]
            children: dict[str | None, list[str]] = {}
            for v in items:
                children.setdefault(v["parent_id"], []).append(v["id"])
            for v in items:
                v["children"] = children.get(v["id"], [])
            leaves = [v["id"] for v in items if not v["children"]]
            return {"block_id": block_id, "head": b["head"], "versions": items, "branches": leaves}

    def checkout(self, block_id: str, version_id: str) -> dict:
        with self._lock:
            b = self.get_block(block_id)
            v = self._one("SELECT id FROM versions WHERE id = ? AND block_id = ?", (version_id, block_id))
            if v is None:
                raise NotFound(f"version {version_id} not found on block {block_id}")
            self._db.execute("UPDATE blocks SET head = ? WHERE id = ?", (version_id, block_id))
            self._bump(b["doc_id"])
            self._db.commit()
            return self.get_block(block_id)

    def step(self, block_id: str, direction: str) -> dict:
        """'back' -> parent of head; 'forward' -> newest child of head."""
        with self._lock:
            tree = self.versions(block_id)
            by_id = {v["id"]: v for v in tree["versions"]}
            head = by_id[tree["head"]]
            if direction == "back":
                if head["parent_id"] is None:
                    raise Invalid("already at the first version")
                return self.checkout(block_id, head["parent_id"])
            if direction == "forward":
                if not head["children"]:
                    raise Invalid("already at the newest version on this branch")
                return self.checkout(block_id, head["children"][-1])
            raise Invalid("direction must be 'back' or 'forward'")

    # --------------------------------------------------------------- comments
    def _comment_dict(self, r: sqlite3.Row) -> dict:
        return {
            "id": r["id"], "doc_id": r["doc_id"], "block_id": r["block_id"], "body": r["body"],
            "author": r["author"], "for_agent": bool(r["for_agent"]), "status": r["status"],
            "replies": json.loads(r["replies"]), "created_at": r["created_at"],
            "resolved_at": r["resolved_at"],
        }

    def add_comment(self, doc_id: str, body: str, *, block_id: str | None = None,
                    author: str = "user", for_agent: bool = True) -> dict:
        with self._lock:
            self.get_document(doc_id)
            if block_id is not None:
                self.block_in(doc_id, block_id)
            if not body.strip():
                raise Invalid("comment body is empty")
            cid = _id("cmt")
            self._db.execute(
                "INSERT INTO comments (id, doc_id, block_id, body, author, for_agent, status, created_at)"
                " VALUES (?,?,?,?,?,?, 'open', ?)",
                (cid, doc_id, block_id, body.strip(), author, int(for_agent), time.time()),
            )
            self._bump(doc_id)
            self._db.commit()
            return self.get_comment(cid)

    def get_comment(self, comment_id: str) -> dict:
        with self._lock:
            r = self._one("SELECT * FROM comments WHERE id = ?", (comment_id,))
            if r is None:
                raise NotFound(f"comment {comment_id} not found")
            return self._comment_dict(r)

    def list_comments(self, doc_id: str, *, include_resolved: bool = False,
                      block_id: str | None = None) -> list[dict]:
        """Comments in document order, top to bottom (block reading order, then time)."""
        with self._lock:
            rows = self._q("SELECT * FROM comments WHERE doc_id = ? ORDER BY created_at", (doc_id,))
            comments = [self._comment_dict(r) for r in rows]
            if not include_resolved:
                comments = [c for c in comments if c["status"] == "open"]
            if block_id is not None:
                comments = [c for c in comments if c["block_id"] == block_id]
            order = {b["id"]: i for i, b in enumerate(self.list_blocks(doc_id))}
            # Document-level comments (no block) come first; comments on deleted blocks last.
            return sorted(
                comments,
                key=lambda c: (-1 if c["block_id"] is None else order.get(c["block_id"], 10**9), c["created_at"]),
            )

    def reply_comment(self, comment_id: str, body: str, *, author: str = "user",
                      resolve: bool = False) -> dict:
        with self._lock:
            c = self.get_comment(comment_id)
            replies = c["replies"] + ([{"author": author, "body": body, "at": time.time()}] if body else [])
            status = "resolved" if resolve else c["status"]
            self._db.execute(
                "UPDATE comments SET replies = ?, status = ?, resolved_at = ? WHERE id = ?",
                (json.dumps(replies), status, time.time() if resolve else c["resolved_at"], comment_id),
            )
            self._bump(c["doc_id"])
            self._db.commit()
            return self.get_comment(comment_id)

    def set_comment_status(self, comment_id: str, status: str) -> dict:
        if status not in ("open", "resolved"):
            raise Invalid("status must be 'open' or 'resolved'")
        with self._lock:
            c = self.get_comment(comment_id)
            self._db.execute(
                "UPDATE comments SET status = ?, resolved_at = ? WHERE id = ?",
                (status, time.time() if status == "resolved" else None, comment_id),
            )
            self._bump(c["doc_id"])
            self._db.commit()
            return self.get_comment(comment_id)

    def delete_comment(self, comment_id: str) -> None:
        with self._lock:
            c = self.get_comment(comment_id)
            self._db.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
            self._bump(c["doc_id"])
            self._db.commit()

    # ------------------------------------------------------------ suggestions
    def _sugg_dict(self, r: sqlite3.Row) -> dict:
        payload = json.loads(r["payload"])
        if isinstance(payload, dict) and isinstance(payload.get("content"), dict):
            t = payload.get("type", "text")
            if r["kind"] == "edit" and r["block_id"]:
                row = self._one("SELECT type FROM blocks WHERE id = ?", (r["block_id"],))
                t = row["type"] if row else t
            payload["content"] = repair_content(t, payload["content"])
        out = {
            "id": r["id"], "doc_id": r["doc_id"], "block_id": r["block_id"], "kind": r["kind"],
            "payload": payload, "status": r["status"], "comment_id": r["comment_id"],
            "note": r["note"], "author": r["author"], "created_at": r["created_at"],
            "decided_at": r["decided_at"], "outdated": False,
        }
        if r["status"] == "pending":
            gone = self._gone(r["block_id"], payload, r["kind"])
            if gone:  # nothing left to apply it to: it can only be dismissed (gh #39)
                out["gone"] = gone
                return out
        # A pending edit/delete whose block moved on since it was proposed is outdated (gh #37).
        # Suggestions stored before 0.5.15 carry no base version and are never outdated.
        base = payload.get("base_version") if isinstance(payload, dict) else None
        if r["status"] == "pending" and base and r["kind"] in ("edit", "delete") and r["block_id"]:
            row = self._one("SELECT type, head FROM blocks WHERE id = ?", (r["block_id"],))
            if row is not None and row["head"] != base:
                out["outdated"] = True
                v = self._one("SELECT content FROM versions WHERE id = ?", (base,))
                if v is not None:
                    out["base_content"] = repair_content(row["type"], json.loads(v["content"]))
        return out

    def _gone(self, block_id: str | None, payload: Any, kind: str) -> str:
        """Why a pending suggestion can't be applied any more, or ''."""
        def missing(bid: str) -> bool:
            row = self._one("SELECT deleted FROM blocks WHERE id = ?", (bid,))
            return row is None or row["deleted"] == 1
        if block_id and missing(block_id):
            return f"block {block_id} was deleted"
        after = payload.get("after") if isinstance(payload, dict) else None
        if kind in ("insert", "move") and after not in (None, "__end__") and missing(after):
            return f"block {after}, which it was to follow, was deleted"
        return ""

    def propose(
        self, doc_id: str, kind: str, payload: dict, *, block_id: str | None = None,
        comment_id: str | None = None, note: str = "", author: str = "agent",
    ) -> dict:
        """Record an agent change. With approval 'auto' it is applied immediately."""
        if kind not in ("edit", "insert", "delete", "move"):
            raise Invalid("kind must be edit, insert, delete or move")
        with self._lock:
            doc = self.get_document(doc_id)
            if block_id is not None:
                self.block_in(doc_id, block_id)
            if comment_id is not None and self.get_comment(comment_id)["doc_id"] != doc_id:
                raise Invalid(f"comment {comment_id} belongs to another document")
            if kind in ("insert", "move"):
                payload = self._anchor(doc_id, kind, payload)
            after = payload.get("after")
            if kind in ("insert", "move") and after not in (None, "__end__"):
                self.block_in(doc_id, after)
            if kind in ("edit", "delete", "move") and block_id is None:
                raise Invalid(f"a {kind} suggestion needs a block_id")
            if kind in ("edit", "insert") and payload.get("content") is not None:
                # Check the suggestion's content now, not at accept time (gh #34).
                _check_content(payload["content"])
                t = (payload.get("type", "text") if kind == "insert"
                     else self.get_block(block_id)["type"] if block_id else None)
                if t in BLOCK_TYPES:
                    payload = {**payload, "content": _check_fields(t, payload["content"])}
            if kind in ("edit", "delete"):
                # The version the agent saw, so a later author edit marks this outdated (gh #37).
                payload = {**payload, "base_version": self.get_block(block_id)["head"]}
            if kind == "insert":
                t = payload.get("type", "text")
                if t not in BLOCK_TYPES:
                    raise Invalid(f"unknown block type {t!r}; expected one of {', '.join(BLOCK_TYPES)}")
                lay = self._inherit_role(payload.get("after", "__end__"), payload.get("layout"))
                if lay is not None:
                    payload = {**payload, "layout": lay}
            self._refuse_repeat(doc_id, kind, payload, block_id)
            # Supersede an older pending suggestion of the same kind on the same block.
            if block_id is not None and kind in ("edit", "delete", "move"):
                self._db.execute(
                    "UPDATE suggestions SET status = 'superseded', decided_at = ?"
                    " WHERE block_id = ? AND kind = ? AND status = 'pending'",
                    (time.time(), block_id, kind),
                )
            sid = _id("sug")
            self._db.execute(
                "INSERT INTO suggestions (id, doc_id, block_id, kind, payload, status, comment_id, note,"
                " author, created_at) VALUES (?,?,?,?,?, 'pending', ?,?,?,?)",
                (sid, doc_id, block_id, kind, json.dumps(payload), comment_id, note, author, time.time()),
            )
            self._bump(doc_id)
            self._db.commit()
            if doc["settings"].get("approval") == "auto":
                return self.accept_suggestion(sid, decided_by="auto")
            return self.get_suggestion(sid)

    # -- anchors: where a pending insert or move will land ------------------------
    #
    # ``payload.after`` is always a block id, None (the start) or "__end__", so every
    # reader of a suggestion can place it. A suggestion that must directly follow a
    # *pending insert* also carries ``payload.after_suggestion`` (that insert's id) and
    # copies its ``after``. That keeps a batch in order: accepted in sequence, each lands
    # after the one before it; accepted out of sequence, it lands after the shared anchor
    # and the earlier ones still land in front of it when they are accepted later.
    def _pending_rows(self, doc_id: str) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM suggestions WHERE doc_id = ? AND status = 'pending'"
                       " ORDER BY created_at, rowid", (doc_id,))

    def _anchor(self, doc_id: str, kind: str, payload: dict) -> dict:
        """Resolve the ``after`` of a new insert or move (see above)."""
        after = payload.get("after", "__end__" if kind == "insert" else None)
        follow = payload.get("after_suggestion")
        if isinstance(after, str) and after.startswith("sug_"):
            follow, after = after, None
        if follow:
            r = self._one("SELECT * FROM suggestions WHERE id = ? AND doc_id = ?", (follow, doc_id))
            if r is None:
                raise NotFound(f"suggestion {follow} not found in document {doc_id}")
            fp = json.loads(r["payload"])
            if r["kind"] != "insert":
                raise Invalid(f"{follow} is a {r['kind']} suggestion; only a block or a pending insert "
                              "can be followed")
            if r["status"] in ("accepted", "applied"):
                bid = (fp.get("result") or {}).get("block_id")
                row = self._one("SELECT deleted FROM blocks WHERE id = ?", (bid,)) if bid else None
                if row is None or row["deleted"]:
                    raise Invalid(f"suggestion {follow} was accepted but its block no longer exists; "
                                  "call get_document")
                return {**{k: v for k, v in payload.items() if k != "after_suggestion"}, "after": bid}
            if r["status"] != "pending":
                raise Invalid(f"suggestion {follow} was {r['status']}, so nothing can be placed after it; "
                              "call get_document to see the current outline")
            return {**payload, "after": fp.get("after", "__end__"), "after_suggestion": follow}
        payload = {k: v for k, v in payload.items() if k != "after_suggestion"}
        if kind == "insert" and after == "__end__":
            # "At the end" means after everything already proposed for the end, in order.
            role = (payload.get("layout") or {}).get("role") or "flow"
            followed, tails = set(), []
            for r in self._pending_rows(doc_id):
                rp = json.loads(r["payload"])
                if rp.get("after_suggestion"):
                    followed.add(rp["after_suggestion"])
                if r["kind"] == "insert" and rp.get("after", "__end__") == "__end__" and (
                        (rp.get("layout") or {}).get("role") or "flow") == role:
                    tails.append(r["id"])
            tails = [t for t in tails if t not in followed]
            if tails:
                return {**payload, "after": "__end__", "after_suggestion": tails[-1]}
        return {**payload, "after": after}

    def _placed(self, s: dict, block_id: str) -> None:
        """A pending insert or move was just applied and put ``block_id`` in place."""
        doc_id, p = s["doc_id"], s["payload"]
        rows = [(r, json.loads(r["payload"])) for r in self._pending_rows(doc_id) if r["id"] != s["id"]]
        if s["kind"] == "insert":
            # What waited to follow it now follows the real block, and so does everything
            # queued behind those (each copies the anchor of the run it belongs to).
            behind, grew = {s["id"]}, True
            while grew:
                grew = False
                for r, rp in rows:
                    if r["id"] not in behind and rp.get("after_suggestion") in behind:
                        behind.add(r["id"])
                        grew = True
            for i, (r, rp) in enumerate(rows):
                if r["id"] in behind:
                    if rp.get("after_suggestion") == s["id"]:
                        rp = {k: v for k, v in rp.items() if k != "after_suggestion"}
                    rp = {**rp, "after": block_id}
                    rows[i] = (r, rp)
                    self._db.execute("UPDATE suggestions SET payload = ? WHERE id = ?", (json.dumps(rp), r["id"]))
        if p.get("after_suggestion") and p.get("after", "__end__") == "__end__":
            # Applied ahead of the pending inserts it was to follow, at the end. Pin those to
            # the block that now precedes it, or they would land behind it later.
            by_id = {r["id"]: (r, rp) for r, rp in rows}
            chain, cur = [], p["after_suggestion"]
            while cur in by_id and cur not in chain:
                chain.append(cur)
                cur = by_id[cur][1].get("after_suggestion")
            if chain:
                role = self.get_block(block_id)["layout"].get("role", "flow")
                prev = None
                for b in self.list_blocks(doc_id):
                    if b["id"] == block_id:
                        break
                    if b["layout"].get("role", "flow") == role:
                        prev = b["id"]
                for cid in chain:
                    _, rp = by_id[cid]
                    if rp.get("after", "__end__") == "__end__":
                        new = {**rp, "after": prev}
                        if prev is None:  # the start of the document: say which page it belongs to
                            new["layout"] = {**(rp.get("layout") or {}), "role": role}
                        self._db.execute("UPDATE suggestions SET payload = ? WHERE id = ?", (json.dumps(new), cid))

    def _unchain(self, s: dict) -> None:
        """A pending insert was rejected or withdrawn: what was to follow it now follows
        whatever it was to follow."""
        if s["kind"] != "insert":
            return
        p = s["payload"]
        for r in self._pending_rows(s["doc_id"]):
            rp = json.loads(r["payload"])
            if rp.get("after_suggestion") == s["id"]:
                rp = {k: v for k, v in rp.items() if k != "after_suggestion"}
                if p.get("after_suggestion"):
                    rp["after_suggestion"] = p["after_suggestion"]
                self._db.execute("UPDATE suggestions SET payload = ? WHERE id = ?",
                                 (json.dumps({**rp, "after": p.get("after", "__end__")}), r["id"]))

    def _refuse_repeat(self, doc_id: str, kind: str, payload: dict, block_id: str | None) -> None:
        """Raise :class:`NoChange` for a suggestion that changes nothing or that repeats one
        still pending. An agent that can't tell its change is waiting tends to send it again."""
        def same(a: Any, b: Any) -> bool:
            return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

        pending = [(r, json.loads(r["payload"])) for r in self._pending_rows(doc_id)]
        if kind == "edit":
            b = self.get_block(block_id)
            c = payload.get("content")
            new_content = _merge(b["content"], c) if c is not None else b["content"]
            for k in ("rows", "spec"):
                if c and k in c:
                    new_content[k] = c[k]
            new_refs = _clean_refs(payload["refs"]) if payload.get("refs") is not None else b["refs"]
            lay = payload.get("layout") or {}
            new_layout = _merge(b["layout"], lay) if lay else b["layout"]
            for r, rp in pending:
                if r["kind"] == "edit" and r["block_id"] == block_id and all(
                        same(rp.get(k), payload.get(k)) for k in ("content", "refs", "layout")):
                    raise NoChange(
                        f"Already proposed: suggestion {r['id']} makes exactly this edit to {block_id} and is "
                        "still waiting for the author. Nothing new was recorded; do not send it again.")
            if same(new_content, b["content"]) and same(new_refs, b["refs"]) and same(new_layout, b["layout"]):
                raise NoChange(
                    f"No change: {block_id} already has exactly this content, so no suggestion was recorded. "
                    "Read the block (read_blocks) if you expected something else.")
        elif kind == "delete":
            for r, _ in pending:
                if r["kind"] == "delete" and r["block_id"] == block_id:
                    raise NoChange(f"Already proposed: suggestion {r['id']} deletes {block_id} and is still "
                                   "waiting for the author. Nothing new was recorded.")
        elif kind == "move":
            for r, rp in pending:
                if (r["kind"] == "move" and r["block_id"] == block_id and rp.get("after") == payload.get("after")
                        and rp.get("after_suggestion") == payload.get("after_suggestion")):
                    raise NoChange(f"Already proposed: suggestion {r['id']} moves {block_id} to the same place "
                                   "and is still waiting for the author. Nothing new was recorded.")
            if not payload.get("after_suggestion"):
                ids = [b["id"] for b in self.list_blocks(doc_id)]
                i = ids.index(block_id)
                if (ids[i - 1] if i else None) == payload.get("after"):
                    where = f"after {payload['after']}" if payload.get("after") else "at the start"
                    raise NoChange(f"No change: {block_id} is already right {where}.")
        elif kind == "insert":
            t = payload.get("type", "text")
            c = payload.get("content") if isinstance(payload.get("content"), dict) else {}
            # Two dividers or page breaks in a row are deliberate; two identical paragraphs
            # at the same anchor are the same draft sent twice.
            if t in ("divider", "page-break", "spacer") or not (plain_text(t, c) or c.get("src") or c.get("spec")):
                return
            for r, rp in pending:
                if r["kind"] == "insert" and all(
                        same(rp.get(k), payload.get(k)) for k in ("type", "content", "refs", "after")):
                    raise NoChange(
                        f"Already proposed: suggestion {r['id']} inserts this same {payload.get('type', 'text')} "
                        "block and is still waiting for the author. Nothing new was recorded; to change it, "
                        f"edit {r['id']} instead of inserting again.")

    def revise_suggestion(self, sid: str, *, content: dict | None = None, refs: list | None = None,
                          layout: dict | None = None, note: str | None = None) -> dict:
        """Change a pending insert before the author decides on it (the agent fixing its own
        draft). The suggestion keeps its id and its place."""
        with self._lock:
            s = self.get_suggestion(sid)
            if s["status"] != "pending" or s["kind"] != "insert":
                what = f"a pending {s['kind']}" if s["status"] == "pending" else s["status"]
                raise Invalid(f"suggestion {sid} is {what}; only a pending insert can be revised")
            p = dict(s["payload"])
            t = p.get("type", "text")
            if content is not None:
                _check_content(content)
                merged = _merge(p.get("content") or {}, _check_fields(t, content) or {})
                for k in ("rows", "spec"):
                    if k in content:
                        merged[k] = content[k]
                p["content"] = merged
            if refs is not None:
                p["refs"] = refs
            if layout:
                cols = self.get_document(s["doc_id"])["settings"]["columns"]
                lay = _merge(p.get("layout") or {}, layout)
                check_variant(t, validate_layout(lay, cols))
                p["layout"] = lay
            if json.dumps(p, sort_keys=True) == json.dumps(s["payload"], sort_keys=True) and note in (None, s["note"]):
                raise NoChange(f"No change: pending insert {sid} already has exactly this content.")
            self._db.execute("UPDATE suggestions SET payload = ?, note = ? WHERE id = ?",
                             (json.dumps(p), s["note"] if note is None else note, sid))
            self._bump(s["doc_id"])
            self._db.commit()
            return self.get_suggestion(sid)

    def withdraw_suggestion(self, sid: str) -> dict:
        """The agent takes back one of its own pending suggestions."""
        return self.reject_suggestion(sid, WITHDRAWN)

    @staticmethod
    def accept_order(suggestions: list[dict]) -> list[dict]:
        """Pending suggestions in an order that is safe to apply one after another: an insert
        or move comes after the pending insert it follows, and after any pending move of the
        block it is anchored to. Otherwise oldest first."""
        by_id = {s["id"]: s for s in suggestions}
        moves = {s["block_id"]: s["id"] for s in suggestions if s["kind"] == "move"}
        out: list[dict] = []
        state: dict[str, int] = {}

        def visit(s: dict) -> None:
            if state.get(s["id"]):
                return  # done, or a cycle (two moves after each other): keep creation order
            state[s["id"]] = 1
            p = s["payload"] if isinstance(s["payload"], dict) else {}
            if s["kind"] in ("insert", "move"):
                for dep in (p.get("after_suggestion"), moves.get(p.get("after"))):
                    if dep and dep != s["id"] and dep in by_id:
                        visit(by_id[dep])
            state[s["id"]] = 2
            out.append(s)

        for s in suggestions:
            visit(s)
        return out

    def accept_all(self, doc_id: str) -> dict:
        """Accept every pending suggestion that can be applied, in a safe order. Nothing is
        dropped silently: each one left behind is listed with the reason."""
        with self._lock:
            self.get_document(doc_id)
            n, stale, gone, failed = 0, [], [], []
            for s in self.accept_order(self.list_suggestions(doc_id)):
                cur = self.get_suggestion(s["id"])  # earlier accepts in this loop may have changed it
                if cur["status"] != "pending":
                    p = cur["payload"] if isinstance(cur["payload"], dict) else {}
                    why = p.get("obsolete_reason") or cur["status"]
                    failed.append({"id": s["id"], "kind": s["kind"], "block_id": s["block_id"],
                                   "reason": f"closed while the others were accepted ({why})"})
                    continue
                if cur.get("gone"):
                    gone.append(s["id"])  # its block was deleted: only Reject can clear it (gh #39)
                    continue
                if cur["outdated"]:
                    stale.append(s["id"])  # never apply a stale edit unseen (gh #37)
                    continue
                try:
                    self.accept_suggestion(s["id"])
                    n += 1
                except (Invalid, NotFound) as exc:
                    failed.append({"id": s["id"], "kind": s["kind"], "block_id": s["block_id"],
                                   "reason": str(exc.args[0] if exc.args else exc)})
            return {"ok": True, "count": n, "skipped": stale + gone + [f["id"] for f in failed],
                    "skipped_gone": gone, "failed": failed}

    def preview(self, doc_id: str) -> dict:
        """The document as it would read if every pending suggestion that can be applied
        were accepted now. Nothing is written.

        ``entries`` are the blocks in reading order, each ``{"block", "pending": [suggestions],
        "deleted": bool, "accepted": block | None}``: a pending insert's block id is its
        suggestion id, ``accepted`` is the block as the author has it when an edit is
        pending, and a block with a pending delete stays in the list, flagged. ``left_out``
        lists the pending suggestions that would not apply, with the reason."""
        with self._lock:
            doc = self.get_document(doc_id)
            cols = doc["settings"]["columns"]
            seq = [{"block": b, "pending": [], "deleted": False, "accepted": None}
                   for b in sorted(self.list_blocks(doc_id), key=lambda b: b["ord"])]
            left_out: list[dict] = []

            def find(bid: str | None) -> int:
                return next((i for i, e in enumerate(seq) if e["block"]["id"] == bid), -1)

            def place(entry: dict, p: dict, inherit: bool) -> bool:
                follow = find(p.get("after_suggestion")) if p.get("after_suggestion") else -1
                after = p.get("after", "__end__")
                if follow >= 0:
                    i = follow + 1
                elif after is None:
                    i = 0
                elif after == "__end__":
                    i = len(seq)
                else:
                    i = find(after) + 1
                    if i == 0:
                        return False
                if inherit and i > 0 and (follow >= 0 or after not in (None, "__end__")):
                    role = seq[i - 1]["block"]["layout"].get("role")  # the rule of _inherit_role
                    if role in ("cover", "back"):
                        entry["block"]["layout"]["role"] = role
                seq.insert(i, entry)
                return True

            for s in self.accept_order(self.list_suggestions(doc_id)):
                p = s["payload"] if isinstance(s["payload"], dict) else {}
                if s.get("gone") or s["outdated"]:
                    left_out.append({"id": s["id"], "kind": s["kind"], "block_id": s["block_id"],
                                     "reason": s.get("gone") or "the block was edited after this was proposed"})
                    continue
                try:
                    if s["kind"] == "insert":
                        t = p.get("type", "text")
                        content, lay = check_block(t, p.get("content"), p.get("layout"), cols)
                        blk = {"id": s["id"], "doc_id": doc_id, "type": t, "ord": 0.0, "layout": lay,
                               "content": repair_content(t, _merge(DEFAULT_CONTENT[t], content or {})),
                               "refs": _clean_refs(p.get("refs")), "head": "", "n_versions": 0,
                               "author": s["author"], "updated_at": s["created_at"]}
                        entry = {"block": blk, "pending": [s], "deleted": False, "accepted": None}
                        if not place(entry, p, inherit=not (p.get("layout") or {}).get("role")):
                            raise Invalid(f"block {p.get('after')}, which it was to follow, is gone")
                        continue
                    i = find(s["block_id"])
                    if i < 0 or seq[i]["deleted"]:
                        raise Invalid(f"block {s['block_id']} is deleted by an earlier pending suggestion")
                    e = seq[i]
                    if s["kind"] == "edit":
                        b = e["block"]
                        c = _check_fields(b["type"], p.get("content"))
                        new = _merge(b["content"], c) if c is not None else b["content"]
                        for k in ("rows", "spec"):
                            if c and k in c:
                                new[k] = c[k]
                        lay = b["layout"]
                        if p.get("layout"):
                            lay = validate_layout(_merge(b["layout"], p["layout"]), cols)
                            check_variant(b["type"], lay)
                        e["accepted"] = e["accepted"] or b
                        e["block"] = {**b, "content": new, "layout": lay,
                                      "refs": _clean_refs(p["refs"]) if p.get("refs") is not None else b["refs"]}
                    elif s["kind"] == "delete":
                        e["deleted"] = True
                    elif s["kind"] == "move":
                        seq.pop(i)
                        if not place(e, p, inherit=False):
                            seq.insert(i, e)
                            raise Invalid(f"block {p.get('after')}, which it was to follow, is gone")
                    e["pending"].append(s)
                except (Invalid, NotFound, TypeError, ValueError) as exc:
                    left_out.append({"id": s["id"], "kind": s["kind"], "block_id": s["block_id"],
                                     "reason": str(exc.args[0] if exc.args else exc)})
            rank = {"cover": 0, "flow": 1, "back": 2}
            pos = {id(e): i for i, e in enumerate(seq)}
            seq.sort(key=lambda e: (rank.get(e["block"]["layout"].get("role", "flow"), 1), pos[id(e)]))
            return {"doc": doc, "entries": seq, "left_out": left_out,
                    "pending": sum(len(e["pending"]) for e in seq)}

    def preview_full(self, doc_id: str) -> dict:
        """:meth:`full` for the preview: what the print view renders for ``?preview=pending``."""
        with self._lock:
            pv = self.preview(doc_id)
            doc = self.full(doc_id)
            doc["blocks"] = [{**e["block"], "ord": float(i + 1)}
                             for i, e in enumerate(pv["entries"]) if not e["deleted"]]
            doc["suggestions"] = []
            doc["preview"] = {
                "pending": pv["pending"],
                "blocks": {e["block"]["id"]: [s["kind"] for s in e["pending"]]
                           for e in pv["entries"] if e["pending"] and not e["deleted"]},
            }
            return doc

    def changes_since(self, doc_id: str, since: float) -> dict:
        """What happened to the document after ``since`` that the agent did not do itself:
        its suggestions the author decided, blocks the author edited, added or deleted, and
        new open comments."""
        with self._lock:
            out: dict[str, list] = {"accepted": [], "rejected": [], "obsolete": [], "edited": [], "added": [],
                                    "deleted": [], "comments": []}
            agent_deleted = set()
            for r in self._q("SELECT * FROM suggestions WHERE doc_id = ? AND decided_at > ? ORDER BY decided_at",
                             (doc_id, since)):
                p = json.loads(r["payload"])
                item = {"id": r["id"], "kind": r["kind"],
                        "block_id": r["block_id"] or (p.get("result") or {}).get("block_id")}
                if r["status"] == "accepted":
                    out["accepted"].append(item)
                    if r["kind"] == "delete":
                        agent_deleted.add(r["block_id"])
                elif r["status"] == "rejected" and p.get("reject_reason") != WITHDRAWN:
                    out["rejected"].append({**item, "reason": p.get("reject_reason", "")})
                elif r["status"] == "obsolete":
                    out["obsolete"].append({**item, "reason": p.get("obsolete_reason", "")})
            touched = {i["block_id"] for i in out["accepted"]}
            for r in self._q(
                    "SELECT b.id, b.created_at, b.deleted, b.deleted_at,"
                    " (SELECT author FROM versions v WHERE v.block_id = b.id ORDER BY v.created_at, v.rowid LIMIT 1)"
                    " AS first_author,"
                    " (SELECT COUNT(*) FROM versions v WHERE v.block_id = b.id AND v.created_at > ?"
                    " AND v.author != 'agent') AS human_versions"
                    " FROM blocks b WHERE b.doc_id = ? ORDER BY b.ord", (since, doc_id)):
                if r["deleted"] == 1:
                    if (r["deleted_at"] or 0) > since and r["id"] not in agent_deleted:
                        out["deleted"].append(r["id"])
                elif r["created_at"] > since and r["first_author"] != "agent":
                    out["added"].append(r["id"])
                elif r["human_versions"] and r["id"] not in touched:
                    out["edited"].append(r["id"])
            for r in self._q("SELECT id, block_id FROM comments WHERE doc_id = ? AND created_at > ?"
                             " AND status = 'open' ORDER BY created_at", (doc_id, since)):
                out["comments"].append({"id": r["id"], "block_id": r["block_id"]})
            return out

    def get_suggestion(self, sid: str) -> dict:
        with self._lock:
            r = self._one("SELECT * FROM suggestions WHERE id = ?", (sid,))
            if r is None:
                raise NotFound(f"suggestion {sid} not found")
            return self._sugg_dict(r)

    def list_suggestions(self, doc_id: str, *, status: str | None = "pending") -> list[dict]:
        with self._lock:
            if status is None:
                rows = self._q("SELECT * FROM suggestions WHERE doc_id = ? ORDER BY created_at", (doc_id,))
            else:
                rows = self._q(
                    "SELECT * FROM suggestions WHERE doc_id = ? AND status = ? ORDER BY created_at",
                    (doc_id, status),
                )
            return [self._sugg_dict(r) for r in rows]

    def accept_suggestion(self, sid: str, *, payload_override: dict | None = None,
                          decided_by: str = "user", force: bool = False) -> dict:
        """Apply a pending suggestion.

        An outdated one (its block changed after it was proposed) is refused unless
        ``force``; a forced outdated edit lands as a branch off the version the agent saw,
        like a concurrent human edit, so the author's newer version stays in the tree (gh #37).
        """
        with self._lock:
            s = self.get_suggestion(sid)
            if s["status"] != "pending":
                raise Invalid(f"suggestion {sid} is already {s['status']}")
            if s.get("gone"):
                raise Invalid(f"suggestion {sid} can't be applied: {s['gone']}. Reject it to dismiss it")
            if s["outdated"] and not force:
                raise Invalid(
                    f"suggestion {sid} is outdated: the block was edited after it was proposed."
                    " Reject it and ask again, or accept it anyway (force)")
            p = {**s["payload"], **(payload_override or {})}
            note = s["note"] or f"suggestion {sid}"
            result: dict[str, Any] = {}
            if s["kind"] == "edit":
                blk = self.edit_block(s["block_id"], content=p.get("content"), refs=p.get("refs"),
                                      author=s["author"], note=note,
                                      base=s["payload"].get("base_version") if s["outdated"] else None)
                if p.get("layout"):
                    blk = self.set_layout(s["block_id"], p["layout"])  # styling rides along with the edit
                result = {"block_id": blk["id"]}
                if s["outdated"]:
                    result["branched"] = True
            elif s["kind"] == "insert":
                blk = self.insert_block(
                    s["doc_id"], p.get("type", "text"), p.get("content"),
                    after=p.get("after", "__end__"), layout=p.get("layout"), refs=p.get("refs"),
                    author=s["author"], note=note,
                )
                result = {"block_id": blk["id"]}
                self._placed(s, blk["id"])
            elif s["kind"] == "delete":
                self.delete_block(s["block_id"], keep=sid)
            elif s["kind"] == "move":
                self.move_block(s["block_id"], p.get("after"))
                self._placed(s, s["block_id"])
            self._db.execute(
                "UPDATE suggestions SET status = ?, decided_at = ?, payload = ? WHERE id = ?",
                ("accepted" if decided_by != "auto" else "applied", time.time(),
                 json.dumps({**p, "result": result}), sid),
            )
            if s["comment_id"]:
                others = self._one(
                    "SELECT COUNT(*) AS n FROM suggestions WHERE comment_id = ? AND status = 'pending' AND id != ?",
                    (s["comment_id"], sid))["n"]
                if not others:  # the comment's last pending suggestion was accepted
                    self._db.execute(
                        "UPDATE comments SET status = 'resolved', resolved_at = ? WHERE id = ? AND status = 'open'",
                        (time.time(), s["comment_id"]))
            self._bump(s["doc_id"])
            self._db.commit()
            return self.get_suggestion(sid)

    def reject_suggestion(self, sid: str, reason: str = "") -> dict:
        with self._lock:
            s = self.get_suggestion(sid)
            if s["status"] != "pending":
                raise Invalid(f"suggestion {sid} is already {s['status']}")
            p = {**s["payload"], "reject_reason": reason} if reason else s["payload"]
            self._db.execute(
                "UPDATE suggestions SET status = 'rejected', decided_at = ?, payload = ? WHERE id = ?",
                (time.time(), json.dumps(p), sid),
            )
            self._unchain(s)
            self._bump(s["doc_id"])
            self._db.commit()
            return self.get_suggestion(sid)

    # ---------------------------------------------------------------- uploads
    def add_upload(self, doc_id: str, filename: str, data: bytes, *, purpose: str = "reference",
                   mime: str = "application/octet-stream", text: str = "") -> dict:
        if purpose not in UPLOAD_PURPOSES:
            raise Invalid(f"purpose must be one of {', '.join(UPLOAD_PURPOSES)}")
        with self._lock:
            self.get_document(doc_id)
            uid = _id("upl")
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name) or "file"
            path = self.uploads_dir / f"{uid}_{safe}"
            path.write_bytes(data)
            try:
                self._db.execute(
                    "INSERT INTO uploads (id, doc_id, filename, purpose, mime, path, text, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (uid, doc_id, Path(filename).name, purpose, mime, str(path), text, time.time()),
                )
                self._bump(doc_id)
                self._db.commit()
            except BaseException:
                path.unlink(missing_ok=True)  # the row is rolled back; don't orphan the file
                raise
            return self.get_upload(uid)

    def get_upload(self, uid: str, *, with_text: bool = False) -> dict:
        with self._lock:
            r = self._one("SELECT * FROM uploads WHERE id = ?", (uid,))
            if r is None:
                raise NotFound(f"upload {uid} not found")
            meta = json.loads(r["meta"] or "{}")
            d = {"id": r["id"], "doc_id": r["doc_id"], "filename": r["filename"], "purpose": r["purpose"],
                 "mime": r["mime"], "path": r["path"], "chars": len(r["text"]),
                 "created_at": r["created_at"], "url": f"/api/uploads/{r['id']}/file",
                 "pages": meta.get("pages", []), "style": meta.get("style")}
            if with_text:
                d["text"] = r["text"]
            return d

    def set_upload_meta(self, uid: str, meta: dict) -> dict:
        """Rendered page images and the extracted style profile of an upload."""
        with self._lock:
            u = self.get_upload(uid)
            self._db.execute("UPDATE uploads SET meta = ? WHERE id = ?", (json.dumps(meta), uid))
            self._bump(u["doc_id"])
            self._db.commit()
            return self.get_upload(uid)

    def list_uploads(self, doc_id: str) -> list[dict]:
        with self._lock:
            rows = self._q("SELECT id FROM uploads WHERE doc_id = ? ORDER BY created_at", (doc_id,))
            return [self.get_upload(r["id"]) for r in rows]

    def delete_upload(self, uid: str) -> None:
        with self._lock:
            u = self.get_upload(uid)
            self._db.execute("DELETE FROM uploads WHERE id = ?", (uid,))
            for f in [u["path"], *u.get("pages", [])]:
                try:
                    Path(f).unlink(missing_ok=True)
                except OSError:
                    pass
            self._bump(u["doc_id"])
            self._db.commit()

    # ----------------------------------------------------------------- themes
    def save_theme(self, label: str, values: dict, *, author: str = "",
                   explicit: list[str] | None = None) -> str:
        """Save a document's look as a named theme (validated values; see themes.THEME_KEYS).
        ``explicit`` names the style keys the author chose in the document the look came from:
        the section and rule heading variants yield to those, so the look survives the save."""
        from . import themes

        label = (label or "").strip()[:60]
        if not label:
            raise Invalid("a saved theme needs a name")
        try:
            clean = themes.clean_values(values)
        except ValueError as exc:
            raise Invalid(str(exc)) from None
        base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "theme"
        with self._lock:
            taken = set(themes.theme_names())
            name, n = base, 2
            while name in taken:
                name, n = f"{base}-{n}", n + 1
            chosen = sorted({k for k in explicit or [] if k in clean})
            data = {**clean, "label": label, **({"explicit": chosen} if chosen else {})}
            self._db.execute("INSERT INTO themes (name, data, author, created_at) VALUES (?,?,?,?)",
                             (name, json.dumps(data), author, time.time()))
            self._db.commit()
            return name

    def saved_themes(self) -> dict[str, dict]:
        with self._lock:
            return {r["name"]: {**json.loads(r["data"]), "author": r["author"]}
                    for r in self._q("SELECT name, data, author FROM themes ORDER BY created_at")}

    def delete_theme(self, name: str) -> None:
        with self._lock:
            if self._one("SELECT name FROM themes WHERE name = ?", (name,)) is None:
                raise NotFound(f"saved theme {name} not found (only saved themes can be deleted)")
            self._db.execute("DELETE FROM themes WHERE name = ?", (name,))
            self._db.commit()

    # ----------------------------------------------------------------- layout
    def set_layout_report(self, doc_id: str, report: dict) -> None:
        self._layout_reports[doc_id] = report

    def layout_report(self, doc_id: str) -> dict | None:
        return self._layout_reports.get(doc_id)


def _clean_refs(refs: list | None) -> list[dict]:
    out = []
    for r in refs or []:
        if isinstance(r, str):
            r = {"title": r}
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or r.get("url") or "").strip()
        if not title:
            continue
        out.append({"title": title, "url": str(r.get("url") or "").strip(),
                    "note": str(r.get("note") or "").strip()})
    return out


# A process-wide store, set by the server, so agent tools reach the same data.
_STORE: Store | None = None


def set_store(store: Store | None) -> None:
    global _STORE
    _STORE = store


def get_store() -> Store:
    if _STORE is None:
        raise RuntimeError(
            "No codraft store is active. Tools run inside `langstage-codraft run`, "
            "or call langstage_codraft.store.set_store(Store(path)) first."
        )
    return _STORE
