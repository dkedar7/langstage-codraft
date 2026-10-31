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

BLOCK_TYPES = (
    "text", "heading", "image", "video", "table", "chart", "callout", "divider",
    "references", "page-break", "spacer",
)
ROLES = ("flow", "cover", "back")

DEFAULT_SETTINGS: dict[str, Any] = {
    "mode": "paged",                 # paged | pageless
    "page_size": "letter",           # letter | a4 | legal | a5 | slide | custom
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


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _check_content(content: Any) -> None:
    """Block content is an object of fields, e.g. {"markdown": "..."} (gh #28)."""
    if content is not None and not isinstance(content, dict):
        raise Invalid(
            f"content must be an object, e.g. {{\"markdown\": \"...\"}}; got {type(content).__name__}"
        )


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

    def __init__(self, store: "Store"):
        self._rlock = threading.RLock()
        self._store = store
        self._depth = 0

    def __enter__(self) -> "_TxLock":
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
        self._db.commit()
        # Last client-computed layout per document (block -> pages), reported by the UI
        # after it paginates, so the agent's outline can say which page a block is on.
        self._layout_reports: dict[str, dict] = {}

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
    def _check_settings(s: dict) -> None:
        from . import themes

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
                merged["columns"] = int(merged["columns"])
                self._check_settings(merged)
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
        if block_type not in BLOCK_TYPES:
            raise Invalid(f"unknown block type {block_type!r}; expected one of {BLOCK_TYPES}")
        _check_content(content)
        block_id = _id("blk")
        now = time.time()
        lay = validate_layout(layout or {}, columns)
        check_variant(block_type, lay)
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
            "content": json.loads(v["content"]) if v else {},
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

    def delete_block(self, block_id: str) -> None:
        with self._lock:
            b = self.get_block(block_id)
            self._db.execute("UPDATE blocks SET deleted = 1 WHERE id = ?", (block_id,))
            self._bump(b["doc_id"])
            self._db.commit()

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
                {"id": r["id"], "parent_id": r["parent_id"], "content": json.loads(r["content"]),
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
        return {
            "id": r["id"], "doc_id": r["doc_id"], "block_id": r["block_id"], "kind": r["kind"],
            "payload": json.loads(r["payload"]), "status": r["status"], "comment_id": r["comment_id"],
            "note": r["note"], "author": r["author"], "created_at": r["created_at"],
            "decided_at": r["decided_at"],
        }

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
            after = payload.get("after")
            if kind in ("insert", "move") and after not in (None, "__end__"):
                self.block_in(doc_id, after)
            if kind in ("edit", "delete", "move") and block_id is None:
                raise Invalid(f"a {kind} suggestion needs a block_id")
            if kind == "insert":
                t = payload.get("type", "text")
                if t not in BLOCK_TYPES:
                    raise Invalid(f"unknown block type {t!r}; expected one of {BLOCK_TYPES}")
                lay = self._inherit_role(payload.get("after", "__end__"), payload.get("layout"))
                if lay is not None:
                    payload = {**payload, "layout": lay}
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
                          decided_by: str = "user") -> dict:
        with self._lock:
            s = self.get_suggestion(sid)
            if s["status"] != "pending":
                raise Invalid(f"suggestion {sid} is already {s['status']}")
            p = {**s["payload"], **(payload_override or {})}
            note = s["note"] or f"suggestion {sid}"
            result: dict[str, Any] = {}
            if s["kind"] == "edit":
                blk = self.edit_block(s["block_id"], content=p.get("content"), refs=p.get("refs"),
                                      author=s["author"], note=note)
                if p.get("layout"):
                    blk = self.set_layout(s["block_id"], p["layout"])  # styling rides along with the edit
                result = {"block_id": blk["id"]}
            elif s["kind"] == "insert":
                blk = self.insert_block(
                    s["doc_id"], p.get("type", "text"), p.get("content"),
                    after=p.get("after", "__end__"), layout=p.get("layout"), refs=p.get("refs"),
                    author=s["author"], note=note,
                )
                result = {"block_id": blk["id"]}
            elif s["kind"] == "delete":
                self.delete_block(s["block_id"])
            elif s["kind"] == "move":
                self.move_block(s["block_id"], p.get("after"))
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
    def save_theme(self, label: str, values: dict, *, author: str = "") -> str:
        """Save a document's look as a named theme (validated values; see themes.THEME_KEYS)."""
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
            self._db.execute("INSERT INTO themes (name, data, author, created_at) VALUES (?,?,?,?)",
                             (name, json.dumps({**clean, "label": label}), author, time.time()))
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
