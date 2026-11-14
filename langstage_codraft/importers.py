"""Bring an existing document in: a Markdown or Word file becomes a Codraft document.

The mappers turn a file into blocks of the existing types and never touch the layout
engine, so an imported document paginates and exports exactly like one written in the
editor. Anything a mapper can't represent degrades to a paragraph and is listed in
``Imported.notes``; a bad file is refused with a one-line reason (:class:`Invalid`),
never a traceback.

Safety: nothing is fetched (a remote image stays a URL), a local image is only read
through the caller's ``resolve`` (the CLI limits it to the imported file's own folder),
SVG is sanitized, and sizes are capped, including a Word file's unzipped size.

The editor (``POST /api/docs/import``) and ``langstage-codraft import`` share
:func:`import_document`, so they can't drift.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from .store import Invalid, Store

MAX_IMPORT_BYTES = 25 * 1024 * 1024      # the imported file, and each image in it
MAX_BLOCKS = 5000
MAX_IMAGES = 500
MAX_DOCX_UNZIPPED = 300 * 1024 * 1024    # a .docx is a zip: refuse a decompression bomb
MAX_DOCX_PARTS = 5000
MAX_DOCX_XML = 32 * 1024 * 1024          # one XML part of it (the text; pictures are separate)
MAX_MARKDOWN_LINES = 400_000
MAX_ITEMS = 50_000                       # paragraphs, lists, headings... before they become blocks
MARKDOWN_EXTENSIONS = (".md", ".markdown", ".mdown", ".mkd", ".txt")
IMPORT_EXTENSIONS = (*MARKDOWN_EXTENSIONS, ".docx")
SPLITS = ("paragraph", "section")

# Given an image path as the file wrote it, return (filename, bytes); None if there is no
# such file; raise ValueError(reason) to refuse it (for example a path outside the folder).
Resolver = Callable[[str], "tuple[str, bytes] | None"]


@dataclass
class Asset:
    filename: str
    data: bytes
    mime: str


@dataclass
class Imported:
    """What a file maps to. An image block's ``content.src`` is ``asset:<n>`` for the
    n-th entry of ``assets`` (stored as an upload when the document is created), a URL,
    or empty when the image couldn't be included."""

    title: str = ""
    blocks: list[dict] = field(default_factory=list)
    assets: list[Asset] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"blocks": len(self.blocks), "images": len(self.assets), "notes": list(self.notes)}


# ------------------------------------------------------------------------ shared helpers
class _Builder:
    """Collects blocks, image assets, references and notes while a mapper walks a file."""

    def __init__(self, resolve: Resolver | None):
        self.out = Imported()
        self.resolve = resolve
        self._hashes: dict[str, int] = {}
        self._ref_n: dict[str, int] = {}
        self._noted: set[str] = set()
        self.any_refs = False

    def note(self, msg: str) -> None:
        if msg not in self._noted:
            self._noted.add(msg)
            self.out.notes.append(msg)

    def add(self, block_type: str, content: dict | None = None, *, layout: dict | None = None,
            refs: list | None = None, meta: str = "") -> dict:
        b: dict[str, Any] = {"type": block_type, "content": content or {}}
        if layout:
            b["layout"] = layout
        if refs:
            b["refs"] = refs
            self.any_refs = True
        if meta:
            b["meta"] = meta
        self.out.blocks.append(b)
        return b

    def ref_number(self, ref: dict) -> int:
        """The number the References block will give this source: references are listed in
        reading order, de-duplicated by URL (or title)."""
        key = (ref.get("url") or ref["title"]).lower()
        return self._ref_n.setdefault(key, len(self._ref_n) + 1)

    # ---------------------------------------------------------------- images
    def asset(self, filename: str, data: bytes, what: str) -> str | None:
        """Keep an image's bytes; returns its ``asset:<n>`` src, or None (with a note)."""
        if len(data) > MAX_IMPORT_BYTES:
            self.note(f"image {what} is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB and was left out")
            return None
        mime = sniff_image(data)
        if mime is None:
            self.note(f"image {what} is not a PNG, JPEG, GIF, WebP or SVG image and was left out")
            return None
        if mime != "image/svg+xml":
            try:  # the header says it's an image; make sure the rest agrees
                from PIL import Image

                with Image.open(io.BytesIO(data)) as im:
                    im.verify()
            except Exception:  # noqa: BLE001 - Pillow raises many types for a damaged file
                self.note(f"image {what} is damaged and was left out")
                return None
        else:
            from .tools import sanitize_svg

            try:
                data = sanitize_svg(data.decode("utf-8", "replace")).encode("utf-8")
            except ValueError:
                self.note(f"image {what} is not a complete SVG and was left out")
                return None
        digest = hashlib.sha256(data).hexdigest()
        if digest not in self._hashes:
            if len(self.out.assets) >= MAX_IMAGES:
                self.note(f"only the first {MAX_IMAGES} images were imported")
                return None
            ext = _EXT[mime]
            stem = re.sub(r"[^A-Za-z0-9._-]+", "_", PurePosixPath(filename.replace("\\", "/")).stem) or "image"
            self._hashes[digest] = len(self.out.assets)
            self.out.assets.append(Asset(f"{stem[:60]}{ext}", data, mime))
        return f"asset:{self._hashes[digest]}"

    def image_src(self, src: str) -> str:
        """The block ``src`` for an image as the file refers to it ('' if it can't be included)."""
        src = src.strip().strip("<>").strip()
        low = src.lower()
        if low.startswith("data:"):
            m = re.match(r"data:(image/[a-z0-9.+-]+)?(?:;[a-z0-9=-]+)*;base64,(.*)$", src, re.I | re.S)
            if not m:
                self.note("an embedded image that isn't base64 image data was left out")
                return ""
            try:
                data = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=True)
            except (binascii.Error, ValueError):
                self.note("an embedded image with invalid base64 data was left out")
                return ""
            return self.asset("embedded", data, "(embedded)") or ""
        if low.startswith(("http://", "https://", "//")):
            return src  # shown from its URL; the import never fetches it
        if re.match(r"[a-z][a-z0-9+.-]*:", low) and not re.match(r"[a-z]:[\\/]", low):
            self.note(f"image {_short(src)} uses an unsupported address and was left out")
            return ""
        path = unquote(src.split("#", 1)[0].split("?", 1)[0])
        if not path:
            return ""
        if self.resolve is None:
            self.note(f"image {_short(path)} is a local file and wasn't included; add it to its image block")
            return ""
        try:
            found = self.resolve(path)
        except ValueError as exc:
            self.note(f"image {_short(path)} was left out: {exc}")
            return ""
        if found is None:
            self.note(f"image {_short(path)} was not found; add it to its image block")
            return ""
        return self.asset(found[0], found[1], _short(path)) or ""


_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
        "image/svg+xml": ".svg"}


def sniff_image(data: bytes) -> str | None:
    """The image type by its first bytes (never by its name), or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:2048].lstrip(b"\xef\xbb\xbf").lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith((b"<?xml", b"<!doctype svg", b"<!--")) and b"<svg" in head):
        return "image/svg+xml"
    return None


def _short(s: str, n: int = 60) -> str:
    s = s.replace("\n", " ")
    return repr(s if len(s) <= n else s[: n - 1] + "…")


_INLINE_CHARS = re.compile(r"[\\`*_\[\]<>~!]")


def strip_inline(md: str) -> str:
    """Inline Markdown as plain text, for headings, table cells and reference titles."""
    if not _INLINE_CHARS.search(md):
        return " ".join(md.split())
    s = re.sub(r"!\[([^\]\[]*)\]\([^()]*\)", r"\1", md)
    s = re.sub(r"\[([^\]\[]*)\]\([^()]*\)", r"\1", s)
    s = re.sub(r"<(https?://[^<>\s]+)>", r"\1", s)
    s = re.sub(r"</?[a-zA-Z][^<>]*>", "", s)
    if len(s) <= 5000:  # emphasis is matched lazily: keep it to text of a sane length
        s = re.sub(r"(`+)(.+?)\1", r"\2", s)
        s = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"\2", s)
        s = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"\1", s)
        s = re.sub(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])", r"\1", s)
        s = re.sub(r"~~(.+?)~~", r"\1", s)
    s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|<>~])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


_URL = re.compile(r"https?://[^\s<>\")\]]+")


def _ref_from(text_md: str) -> dict | None:
    """A reference ({title, url}) from a footnote's text."""
    m = re.search(r"\[[^\]\[]*\]\((https?://[^)\s]+)", text_md)
    bare = _URL.search(text_md)
    url = (m.group(1) if m else bare.group(0) if bare else "").rstrip(".,;")
    title = strip_inline(text_md)
    if url and title != url:
        title = re.sub(r"\s+", " ", title.replace(url, " ")).strip(" .,;:()<>") or url
    title = title or url
    return {"title": title[:500], "url": url} if title else None


# ------------------------------------------------------------------------------ Markdown
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_QUOTE = re.compile(r"^ {0,3}>")
_LIST = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]+|$)")
_LIST_INTERRUPT = re.compile(r"^ {0,3}(?:[-*+]|1[.)])[ \t]+\S")  # what may interrupt a paragraph
_FOOT_DEF = re.compile(r"^ {0,3}\[\^([^\]\s]+)\]:[ \t]?(.*)$")
_LINK_DEF = re.compile(
    r"""^\ {0,3}\[([^\]^][^\]]*)\]:[ \t]*(<[^>]*>|\S+)(?:[ \t]+("[^"]*"|'[^']*'|\([^)]*\)))?[ \t]*$""")
_TABLE_DELIM = re.compile(r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_PAGE_BREAK = re.compile(
    r"""^\s*(?:\\(?:newpage|pagebreak|clearpage)\b.*|<div[^>]*page-break-(?:after|before)\s*:\s*always[^>]*>\s*(?:</div>)?)\s*$""",
    re.I)
_HTML_BLOCK = re.compile(
    r"^ {0,3}</?(?:div|p|table|thead|tbody|tr|td|th|img|details|summary|figure|figcaption|center|"
    r"picture|source|video|audio|iframe|section|article|aside|header|footer|nav|ul|ol|li|dl|dt|dd|"
    r"blockquote|pre|h[1-6]|hr|br|a|span|style|script|svg)\b", re.I)
_IMG = r"!\[((?:[^\]\[\\]|\\.)*)\]\(\s*(<[^>]*>|(?:[^()\s]|\([^()\s]*\))+)(?:\s+(?:\"([^\"]*)\"|'([^']*)'))?\s*\)"
_IMG_RE = re.compile(_IMG)
_LINKED_IMG_RE = re.compile(r"\[\s*(" + _IMG + r")\s*\]\([^)]*\)")
_HTML_IMG_RE = re.compile(r"<img\b[^<>]*>", re.I)
_ALERT = re.compile(r"^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$", re.I)
_ALERT_TONE = {"note": "note", "tip": "success", "important": "info", "warning": "warn", "caution": "warn"}
_FENCE_LINE = re.compile(r"^[ \t]*(`{3,}|~{3,})", re.M)
_TICKS = re.compile(r"`+")


def _code_ranges(text: str) -> list[tuple[int, int]]:
    """(start, end) of the code in ``text``, in order: fenced blocks, and inline spans
    (a run of backticks up to the next run of the same length). One pass, so a file
    built to make a regex backtrack can't slow it down."""
    fenced: list[tuple[int, int]] = []
    opened: tuple[int, str] | None = None
    for m in _FENCE_LINE.finditer(text):
        if opened is None:
            opened = (m.start(), m.group(1))
        elif m.group(1)[0] == opened[1][0] and len(m.group(1)) >= len(opened[1]):
            eol = text.find("\n", m.end())
            fenced.append((opened[0], len(text) if eol < 0 else eol))
            opened = None
    if opened is not None:
        fenced.append((opened[0], len(text)))
    out: list[tuple[int, int]] = []
    prev = 0
    for start, end in [*fenced, (len(text), len(text))]:
        runs = [(m.start(), m.end()) for m in _TICKS.finditer(text, prev, start)]
        by_len: dict[int, list[int]] = {}
        for idx, (a, z) in enumerate(runs):
            by_len.setdefault(z - a, []).append(idx)
        ptr = dict.fromkeys(by_len, 0)
        i = 0
        while i < len(runs):
            size = runs[i][1] - runs[i][0]
            same, p = by_len[size], ptr[size]
            while p < len(same) and same[p] <= i:
                p += 1
            ptr[size] = p
            if p < len(same):  # the closing run
                out.append((runs[i][0], runs[same[p]][1]))
                i = same[p] + 1
            else:
                i += 1
        if start < end:
            out.append((start, end))
        prev = end
    return out


def _outside_code(text: str, fn: Callable[[str], str]) -> str:
    """Apply ``fn`` to the parts of ``text`` that aren't code (fenced or inline)."""
    out, pos = [], 0
    for start, end in _code_ranges(text):
        out.append(fn(text[pos:start]))
        out.append(text[start:end])
        pos = end
    out.append(fn(text[pos:]))
    return "".join(out)


def _drop_elements(text: str, names: tuple[str, ...] = ("script", "style")) -> str:
    """``text`` without those HTML elements and what they contain (one pass)."""
    low = text.lower()
    out, pos = [], 0
    for m in re.finditer(r"<(" + "|".join(names) + r")\b", low):
        if m.start() < pos:
            continue
        out.append(text[pos:m.start()])
        close = low.find("</" + m.group(1), m.end())
        if close < 0:
            pos = len(text)
            break
        gt = low.find(">", close)
        pos = len(text) if gt < 0 else gt + 1
    out.append(text[pos:])
    return "".join(out)


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    """Leading YAML front matter as simple ``key: value`` pairs, and the rest of the text."""
    if not re.match(r"---[ \t]*\n", text):
        return {}, text
    m = re.search(r"\n(?:---|\.\.\.)[ \t]*(?:\n|$)", text[3:])
    if not m:
        return {}, text
    body = text[3:3 + m.start()]
    lines = [ln for ln in body.split("\n") if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines or not all(re.match(r"^[A-Za-z_][\w.-]*\s*:", ln) or ln[:1] in " \t-" for ln in lines):
        return {}, text  # a thematic break followed by text, not front matter
    meta: dict[str, str] = {}
    for ln in lines:
        km = re.match(r"^([A-Za-z_][\w.-]*)\s*:\s*(.*)$", ln)
        if km:
            v = km.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            elif v.startswith("[") and v.endswith("]"):
                v = ", ".join(p.strip().strip("\"'") for p in v[1:-1].split(",") if p.strip())
            meta[km.group(1).lower()] = v
    return meta, text[3 + m.end():]


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, tick, i = [], [], False, 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "`":
            tick = not tick
        if ch == "|" and not tick:
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur))
    return [strip_inline(c) for c in cells]


def _is_table_start(lines: list[str], i: int) -> bool:
    if i + 1 >= len(lines) or "|" not in lines[i] or not _TABLE_DELIM.match(lines[i + 1]):
        return False
    if "|" not in lines[i + 1] and "-" not in lines[i + 1]:
        return False
    delim = [c for c in lines[i + 1].strip().strip("|").split("|")]
    return len(_split_row(lines[i])) == len(delim)


def _starts_block(lines: list[str], i: int) -> bool:
    """Does line ``i`` start a new block, ending the paragraph above it?"""
    ln = lines[i]
    return bool(_FENCE.match(ln) or _ATX.match(ln) or _HR.match(ln) or _QUOTE.match(ln)
                or _LIST_INTERRUPT.match(ln) or _PAGE_BREAK.match(ln) or _HTML_BLOCK.match(ln)
                or _is_table_start(lines, i))


def _scan(lines: list[str]) -> tuple[list[tuple], dict[str, tuple[str, str]], dict[str, str]]:
    """Block-level pass: raw items, link definitions and footnote definitions."""
    items: list[tuple] = []
    links: dict[str, tuple[str, str]] = {}
    foots: dict[str, str] = {}
    n, i = len(lines), 0
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if len(items) > MAX_ITEMS:
            raise Invalid(f"the file is too long to import (more than {MAX_ITEMS:,} paragraphs)")
        m = _FENCE.match(line)
        if m:
            fence, j = m.group(1), i + 1
            close = re.compile(r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*$")
            while j < n and not close.match(lines[j]):
                j += 1
            items.append(("code", lines[i:j] + [fence]))
            i = j + 1
            continue
        if line.lstrip().startswith("<!--"):
            j = i
            while j < n and "-->" not in lines[j]:
                j += 1
            rest = lines[j].split("-->", 1)[1] if j < n else ""
            i = j + 1
            if rest.strip():
                lines.insert(i, rest)
                n += 1
            continue
        m = _FOOT_DEF.match(line)
        if m:
            body, j = [m.group(2)], i + 1
            while j < n and (lines[j].startswith(("    ", "\t")) or (
                    lines[j].strip() and lines[j - 1].strip() and not _starts_block(lines, j)
                    and not _FOOT_DEF.match(lines[j]) and not _LINK_DEF.match(lines[j]))
                    or (not lines[j].strip() and j + 1 < n and lines[j + 1].startswith(("    ", "\t")))):
                body.append(lines[j].strip())
                j += 1
            foots.setdefault(m.group(1).lower(), " ".join(x for x in body if x))
            i = j
            continue
        m = _LINK_DEF.match(line)
        if m:
            url = m.group(2).strip("<>")
            title = (m.group(3) or "")[1:-1]
            links.setdefault(re.sub(r"\s+", " ", m.group(1).strip().lower()), (url, title))
            i += 1
            continue
        m = _ATX.match(line)
        if m:
            items.append(("heading", len(m.group(1)), m.group(2) or ""))
            i += 1
            continue
        if _HR.match(line):
            items.append(("hr",))
            i += 1
            continue
        if _PAGE_BREAK.match(line):
            items.append(("pagebreak",))
            i += 1
            continue
        if _is_table_start(lines, i):
            j = i + 2
            while j < n and lines[j].strip() and "|" in lines[j]:
                j += 1
            items.append(("table", [lines[i], *lines[i + 2:j]], lines[i + 1]))
            i = j
            continue
        if _QUOTE.match(line):
            j = i
            while j < n and lines[j].strip() and (_QUOTE.match(lines[j]) or not _starts_block(lines, j)):
                j += 1
            items.append(("quote", lines[i:j]))
            i = j
            continue
        if _LIST.match(line):
            j, fenced = i + 1, None
            while j < n:
                ln = lines[j]
                fm = _FENCE.match(ln.lstrip()) if ln.strip() else None
                if fenced:
                    if fm and fm.group(1)[0] == fenced and not fm.group(2).strip():
                        fenced = None
                    j += 1
                    continue
                if fm:
                    if not ln.startswith(" ") and not _LIST.match(ln):
                        break  # a fence at the margin ends the list
                    fenced = fm.group(1)[0]
                    j += 1
                    continue
                if not ln.strip():
                    k = j + 1
                    while k < n and not lines[k].strip():
                        k += 1
                    if k < n and (lines[k].startswith((" ", "\t")) or _LIST.match(lines[k])) and not _HR.match(lines[k]):
                        j = k
                        continue
                    break
                if not ln.startswith((" ", "\t")) and not _LIST.match(ln) and (
                        _ATX.match(ln) or _HR.match(ln) or _QUOTE.match(ln) or _is_table_start(lines, j)
                        or _PAGE_BREAK.match(ln) or _HTML_BLOCK.match(ln)):
                    break
                if _HR.match(ln) and not ln.startswith((" ", "\t")):
                    break
                j += 1
            items.append(("list", lines[i:j]))
            i = j
            continue
        if line.startswith(("    ", "\t")):
            j = i
            while j < n and (lines[j].startswith(("    ", "\t")) or (
                    not lines[j].strip() and j + 1 < n and lines[j + 1].startswith(("    ", "\t")))):
                j += 1
            items.append(("code", lines[i:j]))
            i = j
            continue
        if _HTML_BLOCK.match(line):
            j = i
            while j < n and lines[j].strip():
                j += 1
            items.append(("html", lines[i:j]))
            i = j
            continue
        # A paragraph: up to a blank line or the start of another block. A following
        # === / --- line makes it a (setext) heading.
        j = i + 1
        while j < n and lines[j].strip() and not _SETEXT.match(lines[j]) and not _starts_block(lines, j):
            j += 1
        if j < n and _SETEXT.match(lines[j]):
            level = 1 if lines[j].strip().startswith("=") else 2
            items.append(("heading", level, " ".join(s.strip() for s in lines[i:j])))
            i = j + 1
            continue
        items.append(("para", lines[i:j]))
        i = j
    return items, links, foots


def _attr(tag: str, name: str) -> str:
    m = re.search(rf"""\b{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", tag, re.I)
    return (m.group(1) or m.group(2) or m.group(3) or "") if m else ""


def from_markdown(text: str, *, resolve: Resolver | None = None, split: str = "paragraph") -> Imported:
    """Map Markdown (CommonMark with GFM tables, alerts and footnotes) to blocks.

    ``split`` is how prose becomes text blocks: ``paragraph`` gives each paragraph, list,
    quote and code block its own block (finer version history and suggestions);
    ``section`` keeps the prose between two headings together as one block."""
    if split not in SPLITS:
        raise Invalid(f"split must be one of {', '.join(SPLITS)}")
    b = _Builder(resolve)
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    meta, text = _front_matter(text)
    lines = text.split("\n")
    if len(lines) > MAX_MARKDOWN_LINES:
        raise Invalid(f"the file is too long to import ({len(lines):,} lines; the limit is {MAX_MARKDOWN_LINES:,})")
    items, links, foots = _scan(lines)
    used_foots: set[str] = set()

    def inline(md: str, refs: list[dict]) -> str:
        """Resolve reference-style links and footnote markers (definitions live elsewhere
        in the file, so they wouldn't resolve inside a single block)."""
        def fix(seg: str) -> str:
            def full(m: re.Match) -> str:
                label = re.sub(r"\s+", " ", (m.group(2) or m.group(1)).strip().lower())
                if label not in links:
                    return m.group(0)
                url, title = links[label]
                return f"[{m.group(1)}]({url}" + (f' "{title}"' if title else "") + ")"

            def shortcut(m: re.Match) -> str:
                label = re.sub(r"\s+", " ", m.group(1).strip().lower())
                if label not in links:
                    return m.group(0)
                url, title = links[label]
                return f"[{m.group(1)}]({url}" + (f' "{title}"' if title else "") + ")"

            def foot(m: re.Match) -> str:
                key = m.group(1).lower()
                ref = _ref_from(foots[key]) if key in foots else None
                if ref is None:
                    b.note(f"footnote [^{m.group(1)}] has no definition; its marker was kept as text")
                    return m.group(0)
                used_foots.add(key)
                if ref not in refs:
                    refs.append(ref)
                return f"[{b.ref_number(ref)}]"

            seg = re.sub(r"\[\^([^\]\s]+)\]", foot, seg)
            if links:
                seg = re.sub(r"\[((?:[^\]\[\\]|\\.)+)\]\[([^\]]*)\]", full, seg)
                seg = re.sub(r"(?<![\]\\])\[((?:[^\]\[\\^]|\\.)(?:[^\]\[\\]|\\.)*)\](?![\[(:])", shortcut, seg)
            return seg

        return _outside_code(md, fix)

    def image(alt: str, src: str, caption: str = "") -> None:
        alt = strip_inline(alt)
        b.add("image", {"src": b.image_src(src), "alt": alt, "caption": strip_inline(caption)})

    # Prose waiting to become text blocks: [(markdown, refs)].
    pending: list[tuple[str, list[dict]]] = []

    def flush() -> None:
        if not pending:
            return
        if split == "section":
            refs: list[dict] = []
            for _, r in pending:
                refs += [x for x in r if x not in refs]
            b.add("text", {"markdown": "\n\n".join(md for md, _ in pending)}, refs=refs)
        else:
            for md, refs in pending:
                b.add("text", {"markdown": md}, refs=refs)
        pending.clear()

    def prose(md: str, *, join: bool = False) -> None:
        refs: list[dict] = []
        md = inline(md, refs).strip("\n")
        if not md.strip():
            return
        if join and pending:
            prev, prev_refs = pending[-1]
            pending[-1] = (prev + "\n\n" + md, prev_refs + [r for r in refs if r not in prev_refs])
        else:
            pending.append((md, refs))

    def paragraph(lines: list[str]) -> None:
        md = "\n".join(lines)
        # A paragraph that is only images becomes image blocks.
        bare = _LINKED_IMG_RE.sub("", md)
        bare = _HTML_IMG_RE.sub("", _IMG_RE.sub("", bare))
        if (_IMG_RE.search(md) or _HTML_IMG_RE.search(md)) and not re.sub(r"</?(?:p|div|center|a|br|picture)\b[^<>]*>", "", bare, flags=re.I).strip():
            found = [(m.start(), m.group(1), m.group(2), m.group(3) or m.group(4) or "") for m in _IMG_RE.finditer(md)]
            if len(found) > 1 and not _HTML_IMG_RE.search(md) and all(
                    re.match(r"(?:https?:)?//", f[2].strip("<>"), re.I) for f in found):
                prose(md)  # a row of linked pictures (badges, logos) stays a row
                return
            flush()
            found +=[(m.start(), _attr(m.group(0), "alt"), _attr(m.group(0), "src"), _attr(m.group(0), "title"))
                      for m in _HTML_IMG_RE.finditer(md)]
            for _, alt, src, cap in sorted(found):
                if src:
                    image(alt, src, cap)
            return
        # Images inside a sentence: one stored in the file (a local path or embedded data)
        # can't stay inline, so it follows the paragraph as its own block.
        moved: list[tuple[str, str, str]] = []

        def pull(m: re.Match) -> str:
            src = m.group(2).strip("<>")
            if re.match(r"(?:https?:)?//", src, re.I):
                return m.group(0)
            moved.append((m.group(1), src, m.group(3) or m.group(4) or ""))
            return ""

        md = _outside_code(md, lambda seg: _IMG_RE.sub(pull, seg))
        if moved:
            md = re.sub(r"(?<=\S)[ 	]{2,}(?=\S)", " ", md)
        prose(md)
        if moved:
            flush()
            for alt, src, cap in moved:
                image(alt, src, cap)
            b.note("an image inside a paragraph was placed after it as its own block")

    for idx, item in enumerate(items):
        kind = item[0]
        if kind == "heading":
            flush()
            refs: list[dict] = []
            raw = re.sub(r"\s*\{[#.][^}]*\}\s*$", "", item[2])
            text_ = strip_inline(inline(raw, refs))
            if not text_:
                continue
            first = not b.out.blocks
            b.add("heading", {"text": text_, "level": item[1]}, refs=refs,
                  meta="title" if first and item[1] == 1 else "")
        elif kind == "para":
            paragraph(item[1])
        elif kind == "list":
            # A list introduced by "...:" belongs with that sentence.
            join = bool(pending) and pending[-1][0].rstrip().endswith(":") and idx > 0 and items[idx - 1][0] == "para"
            prose("\n".join(item[1]), join=join)
        elif kind == "code":
            prose("\n".join(item[1]))
        elif kind == "quote":
            body = [re.sub(r"^ {0,3}> ?", "", ln) for ln in item[1]]
            m = _ALERT.match(body[0]) if body else None
            if m:
                flush()
                refs = []
                rest = "\n".join(([m.group(2)] if m.group(2).strip() else []) + body[1:]).strip("\n")
                label = m.group(1).capitalize()
                md = inline(rest, refs)
                b.add("callout", {"markdown": f"**{label}:** {md}" if md.strip() else f"**{label}**",
                                  "tone": _ALERT_TONE[m.group(1).lower()]}, refs=refs)
            else:
                prose("\n".join(item[1]))
        elif kind == "table":
            flush()
            rows = [_split_row(ln) for ln in item[1]]
            width = len(rows[0])
            rows = [(r + [""] * width)[:width] for r in rows]
            # The alignment row (:--, :--:, --:) is kept: a column of figures stays right-aligned.
            marks = [c.strip() for c in item[2].strip().strip("|").split("|")]
            align = ["center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "left"
                     for c in (marks + [""] * width)[:width]]
            content = {"rows": rows, "header": True}
            if any(a != "left" for a in align):
                content["align"] = align
            b.add("table", content)
        elif kind == "hr":
            flush()
            b.add("divider")
        elif kind == "pagebreak":
            flush()
            b.add("page-break")
        elif kind == "html":
            raw = "\n".join(item[1])
            if _HTML_IMG_RE.search(raw) and not re.sub(r"<[^<>]*>", "", raw).strip():
                paragraph(item[1])
                continue
            pics = [(_attr(t, "alt"), _attr(t, "src"), _attr(t, "title")) for t in _HTML_IMG_RE.findall(raw)]
            if any(src for _, src, _ in pics):
                flush()
                for alt, src, cap in pics:
                    if src:
                        image(alt, src, cap)
            plain = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|tr|h[1-6]|summary)>", "\n", raw)
            plain = _drop_elements(plain)
            plain = re.sub(r"<[^<>]+>", "", plain)
            plain = "\n\n".join(s.strip() for s in re.split(r"\n\s*\n|\n", plain) if s.strip())
            if plain:
                prose(plain)
                b.note("an HTML block was imported as plain text")
            elif not pics and re.search(r"<[A-Za-z]", raw):  # not just a closing tag
                b.note("an HTML block with no text was left out")
    flush()

    unused = sorted(set(foots) - used_foots)
    if unused:
        b.note(f"{len(unused)} footnote(s) are never referenced and were left out: "
               + ", ".join(f"[^{k}]" for k in unused[:5]) + ("…" if len(unused) > 5 else ""))
    if b.any_refs:
        b.add("references", {"title": "References"})
        b.note("footnotes became references: each is attached to the block that cites it and listed in a "
               "References block at the end")

    # Title: front matter first, else a leading H1. A front-matter title gets its heading.
    blocks = b.out.blocks
    lead = blocks[0] if blocks and blocks[0].get("meta") == "title" else None
    fm_title = strip_inline(meta.get("title", ""))
    if fm_title and lead is None:
        lead = {"type": "heading", "content": {"text": fm_title, "level": 1}, "meta": "title"}
        blocks.insert(0, lead)
    b.out.title = fm_title or (lead["content"]["text"] if lead else "")
    byline = " · ".join(strip_inline(meta[k]) for k in ("subtitle", "author", "authors", "date") if meta.get(k))
    if byline and lead is not None:
        blocks.insert(blocks.index(lead) + 1,
                      {"type": "text", "content": {"markdown": f"*{byline}*"}, "meta": "subtitle"})
    return b.out


def decode_text(data: bytes, b: Imported | None = None) -> str:
    """A text file's bytes as text; refuses what is plainly not text."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            raise Invalid("the file is not readable text") from None
    if b"\x00" in data[:8192]:
        raise Invalid("the file doesn't look like text (is it really Markdown?)")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        if b is not None:
            b.notes.append("the file is not valid UTF-8; it was read as Windows-1252")
        return data.decode("cp1252", "replace")


# ---------------------------------------------------------------------------------- Word
_MONO = ("consolas", "courier", "menlo", "monaco", "source code", "cascadia", "lucida console",
         "dejavu sans mono", "liberation mono", "fira code", "fira mono", "jetbrains mono", "sf mono",
         "andale mono", "ubuntu mono", "roboto mono", "inconsolata")
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_V = "{urn:schemas-microsoft-com:vml}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _on(el: Any) -> bool:
    """A toggle property (w:b, w:i, ...): present means on unless it says otherwise."""
    return el is not None and (el.get(_W + "val") or "true").lower() not in ("0", "false", "off")


def _md_escape(s: str) -> str:
    s = re.sub(r"([\\`*\[\]<])", r"\\\1", s)
    return re.sub(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", r"\\_", s)


def _check_docx_zip(data: bytes) -> None:
    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise Invalid("this is a legacy Word .doc file; save it as .docx and import that")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            names = {i.filename for i in infos}
    except (zipfile.BadZipFile, OSError, ValueError):
        raise Invalid("not a Word .docx file (it isn't a readable zip archive)") from None
    if "word/document.xml" not in names:
        raise Invalid("not a Word .docx file (it has no word/document.xml)")
    if len(infos) > MAX_DOCX_PARTS or sum(i.file_size for i in infos) > MAX_DOCX_UNZIPPED:
        raise Invalid(f"the Word file is too large to import (over {MAX_DOCX_UNZIPPED // (1024 * 1024)} MB unzipped)")
    if any(i.file_size > MAX_DOCX_XML for i in infos if i.filename.lower().endswith((".xml", ".rels"))):
        raise Invalid(f"the Word file is too large to import (over {MAX_DOCX_XML // (1024 * 1024)} MB of text)")


def from_docx(data: bytes, *, split: str = "paragraph") -> Imported:
    """Map a Word document's structure to blocks: Title and Heading styles, paragraphs
    with bold/italic/strike/code/links, lists, quotes, tables, inline pictures, page
    breaks and footnotes. It imports content and structure, not Word's exact look."""
    if split not in SPLITS:
        raise Invalid(f"split must be one of {', '.join(SPLITS)}")
    _check_docx_zip(data)
    import docx
    from docx.oxml import parse_xml
    from docx.text.paragraph import Paragraph

    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - python-docx raises many types for a damaged file
        raise Invalid(f"could not read the Word file: {type(exc).__name__}: {str(exc)[:160]}") from None
    b = _Builder(None)
    part = d.part

    # Footnotes and endnotes live in their own parts: id -> text.
    notes_text: dict[tuple[str, str], str] = {}
    for rel in part.rels.values():
        kind = "foot" if rel.reltype.endswith("/footnotes") else "end" if rel.reltype.endswith("/endnotes") else ""
        if not kind or rel.is_external:
            continue
        try:
            root = parse_xml(rel.target_part.blob)
        except Exception:  # noqa: BLE001
            continue
        for fn in root.iter(_W + ("footnote" if kind == "foot" else "endnote")):
            if fn.get(_W + "type") in ("separator", "continuationSeparator", "continuationNotice"):
                continue
            txt = "".join(t.text or "" for t in fn.iter(_W + "t")).strip()
            if txt:
                notes_text[(kind, fn.get(_W + "id") or "")] = txt

    styles_seen: dict[str, list[str]] = {}
    kinds_seen: dict[tuple[str, int], str] = {}

    def style_names(p: Any) -> list[str]:
        """The paragraph style's name and its base styles' names, lower-case."""
        ppr = p.find(_W + "pPr")
        ps = ppr.find(_W + "pStyle") if ppr is not None else None
        key = (ps.get(_W + "val") or "") if ps is not None else ""
        if key in styles_seen:
            return styles_seen[key]
        out: list[str] = []
        try:
            st = Paragraph(p, d).style
            for _ in range(6):
                if st is None:
                    break
                out.append((st.name or "").lower())
                st = st.base_style
        except Exception:  # noqa: BLE001 - a style id that doesn't resolve
            pass
        styles_seen[key] = out
        return out

    def list_kind(p: Any, names: list[str]) -> tuple[str, int] | None:
        """('ul' | 'ol', level) for a list paragraph, else None."""
        ppr = p.find(_W + "pPr")
        numpr = ppr.find(_W + "numPr") if ppr is not None else None
        level, num_id = 0, None
        if numpr is not None:
            il, ni = numpr.find(_W + "ilvl"), numpr.find(_W + "numId")
            level = _int(il.get(_W + "val")) if il is not None else 0
            num_id = ni.get(_W + "val") if ni is not None else None
            if num_id == "0":
                return None
        styled = next((n for n in names if n.startswith(("list bullet", "list number", "list continue"))), "")
        if num_id is None and not styled:
            return None
        if styled and num_id is None:
            m = re.search(r"(\d)$", styled)
            level = int(m.group(1)) - 1 if m else 0
            return ("ol" if styled.startswith("list number") else "ul", max(0, level))
        kind = "ol" if styled.startswith("list number") else "ul"
        level = max(0, min(level, 8))
        if (num_id, level) in kinds_seen:
            return (kinds_seen[(num_id, level)], level)
        try:
            numbering = part.numbering_part.element
            num = next(x for x in numbering.iter(_W + "num") if x.get(_W + "numId") == num_id)
            abs_id = num.find(_W + "abstractNumId").get(_W + "val")
            absn = next(x for x in numbering.iter(_W + "abstractNum") if x.get(_W + "abstractNumId") == abs_id)
            lvl = next((x for x in absn.iter(_W + "lvl") if x.get(_W + "ilvl") == str(level)), None)
            fmt = lvl.find(_W + "numFmt").get(_W + "val") if lvl is not None else "bullet"
            kind = "ul" if fmt in ("bullet", "none") else "ol"
        except Exception:  # noqa: BLE001 - no numbering part: fall back to the style
            pass
        kinds_seen[(num_id, level)] = kind
        return (kind, level)

    def run_format(r: Any) -> tuple[bool, bool, bool, bool]:
        rpr = r.find(_W + "rPr")
        if rpr is None:
            return (False, False, False, False)
        st = rpr.find(_W + "rStyle")
        sname = (st.get(_W + "val") or "").lower() if st is not None else ""
        fonts = rpr.find(_W + "rFonts")
        font = ((fonts.get(_W + "ascii") or fonts.get(_W + "hAnsi") or "") if fonts is not None else "").lower()
        code = any(m in font for m in _MONO) or any(k in sname for k in ("code", "verbatim", "preformatted"))
        return (_on(rpr.find(_W + "b")) or sname == "strong", _on(rpr.find(_W + "i")) or sname == "emphasis",
                _on(rpr.find(_W + "strike")) or _on(rpr.find(_W + "dstrike")), code)

    def inlines(el: Any, link: str = "") -> list[tuple]:
        """A paragraph's content in order: ('text', str, fmt, link), ('image', rid),
        ('break',), ('note', kind, id), ('box', element)."""
        out: list[tuple] = []
        for child in el.iterchildren():
            tag = child.tag
            if tag == _W + "r":
                fmt = run_format(child)
                for x in child.iterchildren():
                    t = x.tag
                    if t == _W + "t":
                        out.append(("text", x.text or "", fmt, link))
                    elif t in (_W + "tab", _W + "ptab"):
                        out.append(("text", " ", fmt, link))
                    elif t in (_W + "br", _W + "cr"):
                        if x.get(_W + "type") == "page":
                            out.append(("break",))
                        elif x.get(_W + "type") != "column":
                            out.append(("text", "\n", fmt, link))
                    elif t == _W + "noBreakHyphen":
                        out.append(("text", "-", fmt, link))
                    elif t in (_W + "footnoteReference", _W + "endnoteReference"):
                        out.append(("note", "foot" if t.endswith("footnoteReference") else "end", x.get(_W + "id") or ""))
                    elif t in (_W + "drawing", _W + "pict", _W + "object", _MC + "AlternateContent"):
                        scope = x
                        if t == _MC + "AlternateContent":  # the same shape twice: read one copy
                            scope = x.find(_MC + "Choice")
                            scope = scope if scope is not None else x
                        boxes = list(scope.iter(_W + "txbxContent"))
                        if boxes:
                            out.append(("box", boxes[0]))
                            continue
                        for blip in scope.iter(_A + "blip"):
                            rid = blip.get(_R + "embed")
                            if rid:
                                out.append(("image", rid))
                            elif blip.get(_R + "link"):
                                b.note("a linked (not embedded) picture was left out")
                        for im in scope.iter(_V + "imagedata"):
                            if im.get(_R + "id"):
                                out.append(("image", im.get(_R + "id")))
            elif tag == _W + "hyperlink":
                url = ""
                rid = child.get(_R + "id")
                if rid and rid in part.rels and part.rels[rid].is_external:
                    url = part.rels[rid].target_ref
                    if child.get(_W + "anchor"):
                        url += "#" + child.get(_W + "anchor")
                out += inlines(child, url if re.match(r"(?i)(https?|mailto):", url) else "")
            elif tag in (_W + "del", _W + "moveFrom"):
                b.note("tracked changes were accepted (deleted text was left out)")
            elif tag in (_W + "ins", _W + "moveTo"):
                b.note("tracked changes were accepted (deleted text was left out)")
                out += inlines(child, link)
            elif tag in (_W + "smartTag", _W + "fldSimple", _W + "customXml", _W + "dir", _W + "bdo"):
                out += inlines(child, link)
            elif tag == _W + "sdt":
                content = child.find(_W + "sdtContent")
                if content is not None:
                    out += inlines(content, link)
            elif tag in (_M + "oMath", _M + "oMathPara"):
                txt = "".join(t.text or "" for t in child.iter(_M + "t"))
                if txt:
                    out.append(("text", txt, (False, False, False, False), link))
                b.note("an equation was imported as plain text")
        return out

    def to_markdown(parts: list[tuple], refs: list[dict]) -> str:
        """Runs as inline Markdown. Neighbouring runs with the same format are merged, and
        the markers hug the words (Markdown ignores emphasis around spaces)."""
        merged: list[list] = []
        for p in parts:
            if p[0] == "note":
                txt = notes_text.get((p[1], p[2]))
                ref = _ref_from(txt) if txt else None
                if ref is not None:
                    if ref not in refs:
                        refs.append(ref)
                    merged.append([f"[{b.ref_number(ref)}]", None, ""])
                continue
            if p[0] != "text" or not p[1]:
                continue
            if merged and merged[-1][1] == p[2] and merged[-1][2] == p[3]:
                merged[-1][0] += p[1]
            else:
                merged.append([p[1], p[2], p[3]])
        out: list[str] = []
        for text, fmt, link in merged:
            if fmt is None:  # a reference marker, already Markdown
                out.append(text)
                continue
            lead = text[: len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):] if text.strip() else ""
            core = text.strip()
            if not core:
                out.append(text)
                continue
            bold, italic, strike, code = fmt
            if code and "\n" not in core:
                tick = "``" if "`" in core else "`"
                core = f"{tick}{core}{tick}"
            else:
                core = _md_escape(core)
            core = core.replace("\n", "  \n")
            if strike:
                core = f"~~{core}~~"
            if bold and italic:
                core = f"***{core}***"
            elif bold:
                core = f"**{core}**"
            elif italic:
                core = f"*{core}*"
            if link:
                core = f"[{core}]({link.replace(' ', '%20').replace(')', '%29')})"
            out.append(lead + core + trail)
        md = "".join(out)
        md = re.sub(r"[ \t]+\n", "  \n", md) if "\n" in md else md
        md = md.strip()
        # A line that would read as Markdown structure is plain text here.
        return re.sub(r"(?m)^(#{1,6} |> |[-+] |\d{1,9}[.)] )", lambda m: "\\" + m.group(1), md)

    def plain(parts: list[tuple]) -> str:
        return re.sub(r"\s+", " ", "".join(p[1] for p in parts if p[0] == "text")).strip()

    pending: list[tuple[str, list[dict]]] = []
    list_open = {"on": False}

    def flush() -> None:
        list_open["on"] = False
        if not pending:
            return
        if split == "section":
            refs: list[dict] = []
            for _, r in pending:
                refs += [x for x in r if x not in refs]
            b.add("text", {"markdown": "\n\n".join(md for md, _ in pending)}, refs=refs)
        else:
            for md, refs in pending:
                b.add("text", {"markdown": md}, refs=refs)
        pending.clear()

    def picture(rid: str) -> None:
        try:
            ip = part.related_parts[rid]
        except KeyError:
            b.note("a picture whose data is missing was left out")
            return
        name = str(getattr(ip, "partname", "image")).rsplit("/", 1)[-1]
        src = b.asset(name, ip.blob, repr(name))
        if src is None and re.search(r"\.(emf|wmf)$", name, re.I):
            b.note("EMF/WMF pictures can't be shown in a browser; re-save them as PNG to bring them in")
        if src:
            flush()
            b.add("image", {"src": src, "alt": "", "caption": ""})

    state = {"title": False, "code": False, "toc": False}

    def paragraph(p: Any) -> None:
        names = style_names(p)
        name = names[0] if names else ""
        ppr = p.find(_W + "pPr")
        parts = inlines(p)
        boxes = [x[1] for x in parts if x[0] == "box"]
        if any(n.startswith("toc ") or n == "toc heading" for n in names):
            if not state["toc"]:
                state["toc"] = True
                b.note("the table of contents was left out (it would go stale; headings are imported)")
            return
        if ppr is not None and _on(ppr.find(_W + "pageBreakBefore")) and b.out.blocks:
            flush()
            b.add("page-break")
        text_parts = [x for x in parts if x[0] in ("text", "note")]
        heading = next((n for n in names if n == "title" or re.fullmatch(r"heading [1-9]", n)), "")
        outline = ppr.find(_W + "outlineLvl") if ppr is not None else None
        is_code = any(k in name for k in ("code", "verbatim", "preformatted", "source"))
        if not is_code and state["code"]:
            state["code"] = False
        if plain(parts) or is_code:
            refs: list[dict] = []
            if heading or (outline is not None and (outline.get(_W + "val") or "9").isdigit()
                           and int(outline.get(_W + "val")) < 6 and not list_kind(p, names)):
                flush()
                is_title = False
                if heading == "title":
                    level = 1
                    if not state["title"]:
                        state["title"] = is_title = True
                        b.out.title = plain(parts)
                elif heading:
                    level = int(heading[-1])
                else:
                    level = int(outline.get(_W + "val")) + 1
                for x in parts:  # footnotes on a heading still count as its references
                    if x[0] == "note":
                        to_markdown([x], refs)
                b.add("heading", {"text": plain(parts), "level": max(1, min(level, 6))}, refs=refs,
                      meta="title" if is_title else "")
            elif name == "subtitle":
                flush()
                b.add("text", {"markdown": f"*{to_markdown(text_parts, refs)}*"}, refs=refs,
                      meta="subtitle" if b.out.blocks and b.out.blocks[-1].get("meta") == "title" else "")
            elif name == "caption":
                flush()
                last = b.out.blocks[-1] if b.out.blocks else None
                if last is not None and last["type"] in ("image", "table") and not last["content"].get("caption"):
                    last["content"]["caption"] = plain(parts)
                else:
                    b.add("text", {"markdown": f"*{to_markdown(text_parts, refs)}*"}, refs=refs)
            elif is_code:
                raw = "".join(x[1] for x in parts if x[0] == "text")
                if state["code"] and pending:
                    md, r = pending[-1]
                    pending[-1] = (md[: md.rfind("\n```")] + "\n" + raw + "\n```", r)
                else:
                    list_open["on"] = False
                    pending.append(("```\n" + raw + "\n```", []))
                    state["code"] = True
            else:
                lk = list_kind(p, names)
                md = to_markdown(text_parts, refs)
                if lk:
                    line = "    " * lk[1] + ("1. " if lk[0] == "ol" else "- ") + md.replace("\n", "\n" + "    " * (lk[1] + 1))
                    if list_open["on"] and pending:
                        prev, r = pending[-1]
                        pending[-1] = (prev + "\n" + line, r + [x for x in refs if x not in r])
                    else:
                        intro = bool(pending) and pending[-1][0].rstrip().endswith(":") and not list_open["on"]
                        if intro:
                            prev, r = pending[-1]
                            pending[-1] = (prev + "\n\n" + line, r + [x for x in refs if x not in r])
                        else:
                            pending.append((line, refs))
                        list_open["on"] = True
                else:
                    list_open["on"] = False
                    if any(n in ("quote", "intense quote", "block text") for n in names):
                        md = "\n".join("> " + ln for ln in md.split("\n"))
                    if md:
                        pending.append((md, refs))
        for x in parts:
            if x[0] == "image":
                picture(x[1])
            elif x[0] == "break":
                flush()
                b.add("page-break")
        for box in boxes:
            flush()
            b.note("text box content was placed in the text flow where its anchor is")
            walk(box)
        if ppr is not None and ppr.find(_W + "sectPr") is not None:
            st = ppr.find(_W + "sectPr").find(_W + "type")
            if st is None or st.get(_W + "val") not in ("continuous", "nextColumn"):
                flush()
                b.add("page-break")

    def cell_text(tc: Any) -> str:
        paras = ["".join((t.text or "") if t.tag == _W + "t" else " " for t in p.iter(_W + "t", _W + "tab", _W + "br"))
                 for p in tc.iter(_W + "p")]
        return re.sub(r"[ \t]+", " ", " ".join(s.strip() for s in paras if s.strip()))

    def table(tbl: Any) -> None:
        rows: list[list[str]] = []
        pics: list[str] = []
        trs = [tr for tr in tbl.iterchildren(_W + "tr")]
        cells_all = [list(tr.iterchildren(_W + "tc")) for tr in trs]
        # Content controls can wrap rows and cells.
        for tr, cells in zip(trs, cells_all, strict=True):
            if not cells:
                cells += list(tr.iter(_W + "tc"))
        if len(cells_all) == 1 and len(cells_all[0]) == 1:  # a one-cell table is a box around text
            walk(cells_all[0][0])
            return
        for cells in cells_all:
            row: list[str] = []
            for tc in cells:
                tcpr = tc.find(_W + "tcPr")
                span = tcpr.find(_W + "gridSpan") if tcpr is not None else None
                vmerge = tcpr.find(_W + "vMerge") if tcpr is not None else None
                cont = vmerge is not None and (vmerge.get(_W + "val") or "continue") == "continue"
                row.append("" if cont else cell_text(tc))
                n = int(span.get(_W + "val") or 1) if span is not None and (span.get(_W + "val") or "1").isdigit() else 1
                row += [""] * (min(n, 63) - 1)
                for blip in tc.iter(_A + "blip"):
                    if blip.get(_R + "embed"):
                        pics.append(blip.get(_R + "embed"))
            if row:
                rows.append(row)
        if not rows:
            return
        width = min(max(len(r) for r in rows), 64)
        flush()
        b.add("table", {"rows": [(r + [""] * width)[:width] for r in rows], "header": True})
        for rid in pics:
            picture(rid)
        if pics:
            b.note("a picture inside a table was placed after the table")

    def walk(container: Any) -> None:
        for el in container.iterchildren():
            if el.tag == _W + "p":
                paragraph(el)
            elif el.tag == _W + "tbl":
                table(el)
            elif el.tag == _W + "sdt":
                content = el.find(_W + "sdtContent")
                if content is not None:
                    walk(content)
            elif el.tag in (_W + "ins", _W + "moveTo", _W + "customXml"):
                walk(el)
            if len(b.out.blocks) + len(pending) > MAX_BLOCKS * 2:
                break

    try:
        walk(d.element.body)
    except Invalid:
        raise
    except Exception as exc:  # noqa: BLE001 - a malformed part: a reason, not a traceback
        raise Invalid(f"could not read the Word file: {type(exc).__name__}: {str(exc)[:160]}") from None
    flush()

    blocks = b.out.blocks
    while blocks and blocks[-1]["type"] == "page-break":
        blocks.pop()
    while blocks and blocks[0]["type"] == "page-break":
        blocks.pop(0)
    blocks[:] = [x for i, x in enumerate(blocks)
                 if not (x["type"] == "page-break" and i and blocks[i - 1]["type"] == "page-break")]
    others = [x for x in blocks if x["type"] == "heading" and x.get("meta") != "title"]
    if len(others) < len([x for x in blocks if x["type"] == "heading"]) and any(
            x["content"]["level"] == 1 for x in others):
        # Word's Title sits above Heading 1; Codraft's title is the level-1 heading, so
        # the headings under a Title move down a level (unless they already start at 2).
        for x in others:
            x["content"]["level"] = min(6, x["content"]["level"] + 1)
    for sec in d.sections:
        try:
            parts_hf = [sec.header, sec.footer]
            if any(p.text.strip() for hf in parts_hf if not hf.is_linked_to_previous for p in hf.paragraphs):
                b.note("page headers and footers were not imported; set them in the Design tab")
                break
        except Exception:  # noqa: BLE001
            break
    if b.any_refs:
        blocks.append({"type": "references", "content": {"title": "References"}})
        b.note("footnotes became references: each is attached to the block that cites it and listed in a "
               "References block at the end")
    if not b.out.title:
        try:
            b.out.title = (d.core_properties.title or "").strip()
        except Exception:  # noqa: BLE001
            pass
    return b.out


# ------------------------------------------------------------------------------ dispatch
def read(filename: str, data: bytes, *, resolve: Resolver | None = None, split: str = "paragraph") -> Imported:
    """Map a file to blocks by its extension. Raises :class:`Invalid` with a one-line
    reason for a file that can't be imported."""
    if not isinstance(data, (bytes, bytearray)):
        raise Invalid("the file's content must be bytes")
    ext = Path(filename or "").suffix.lower()
    if ext not in IMPORT_EXTENSIONS:
        hint = "; save it as .docx and import that" if ext in (".doc", ".rtf", ".odt", ".pages") else ""
        raise Invalid(f"can't import a {ext or 'file without an extension'} file: expected Markdown "
                      f"(.md, .markdown, .txt) or Word (.docx){hint}")
    if len(data) > MAX_IMPORT_BYTES:
        raise Invalid(f"the file is too large to import ({MAX_IMPORT_BYTES // (1024 * 1024)} MB max)")
    if not bytes(data).strip():
        raise Invalid("the file is empty: there is nothing to import")
    try:
        if ext == ".docx":
            out = from_docx(bytes(data), split=split)
        else:
            carrier = Imported()
            out = from_markdown(decode_text(bytes(data), carrier), resolve=resolve, split=split)
            out.notes[:0] = carrier.notes
    except Invalid:
        raise
    except Exception as exc:  # noqa: BLE001 - whatever the file holds: a reason, not a traceback
        raise Invalid(f"could not import the file: {type(exc).__name__}: {str(exc)[:160]}") from None
    if not out.blocks:
        raise Invalid("nothing to import: the file has no text, tables or images")
    if len(out.blocks) > MAX_BLOCKS:
        hint = " Try split=section (one text block per section)." if split == "paragraph" else ""
        raise Invalid(f"the file maps to {len(out.blocks)} blocks; the limit is {MAX_BLOCKS}.{hint}")
    return out


def folder_resolver(folder: str | Path) -> Resolver:
    """Read images from the imported file's own folder (and below), nowhere else."""
    root = Path(folder).resolve()

    def resolve(path: str) -> tuple[str, bytes] | None:
        if re.match(r"[A-Za-z]:[\\/]|[\\/]", path):
            raise ValueError("only images in the imported file's folder are read (it is an absolute path)")
        target = (root / path.replace("\\", "/")).resolve()
        if not target.is_relative_to(root):
            raise ValueError("only images in the imported file's folder are read (it is outside it)")
        if not target.is_file():
            return None
        if target.stat().st_size > MAX_IMPORT_BYTES:
            raise ValueError(f"it is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB")
        return target.name, target.read_bytes()

    return resolve


def files_resolver(files: dict[str, bytes]) -> Resolver:
    """Find images among files sent with the import, by file name."""
    by_name = {PurePosixPath(k.replace("\\", "/")).name.lower(): (PurePosixPath(k.replace("\\", "/")).name, v)
               for k, v in files.items()}

    def resolve(path: str) -> tuple[str, bytes] | None:
        return by_name.get(PurePosixPath(path.replace("\\", "/")).name.lower())

    return resolve


def import_document(
    store: Store, cfg: Any, house: dict, filename: str, data: bytes, *, title: str | None = None,
    template: str | None = None, theme: str | None = None, settings: dict | None = None,
    split: str = "paragraph", resolve: Resolver | None = None, author: str = "user",
) -> tuple[dict, Imported]:
    """Create a document from a Markdown or Word file. The template (default blank)
    supplies the look, as for a new document; the file supplies the content. Returns the
    document and what was mapped (``Imported.notes`` lists what needs a look).

    The blocks are the author's own content, not suggestions."""
    from . import templates as tpl
    from .server import new_document

    template_id = template or "blank"
    t = tpl.all_templates().get(template_id)
    if t is None:
        raise Invalid(tpl.unknown_template_message(template_id))
    if settings is not None and not isinstance(settings, dict):
        raise Invalid("settings must be an object")
    wanted = dict(settings or {})
    if theme:
        wanted["theme"] = theme
    imp = read(filename, data, resolve=resolve, split=split)
    _, merged, _ = new_document(cfg, house, template_id, t, None, wanted or None)
    name = (title or "").strip() or imp.title or Path(filename or "").stem.replace("_", " ").strip() or "Imported document"

    # The template's shape applies to the title: on its cover page if the template has
    # one, across the columns if it has several.
    has_cover = any((x.get("layout") or {}).get("role") == "cover" for x in t.get("blocks", []))
    columns = int(merged.get("columns", 1) or 1)
    blocks: list[dict] = []
    for x in imp.blocks:
        meta = x.get("meta")
        blk = {k: v for k, v in x.items() if k != "meta"}
        if meta in ("title", "subtitle"):
            if has_cover:
                blk["layout"] = {**blk.get("layout", {}), "role": "cover"}
            elif columns > 1:
                blk["layout"] = {**blk.get("layout", {}), "span": "full"}
        blocks.append(blk)

    from .store import check_block

    for i, blk in enumerate(blocks):  # refuse before anything is written
        try:
            check_block(blk["type"], blk.get("content"), blk.get("layout"), columns)
        except Invalid as exc:
            raise Invalid(f"block {i + 1} of the import ({blk['type']}) can't be stored: {exc}") from None
    doc = store.create_document(name[:300], merged, [])
    ids: list[str] = []
    try:
        for a in imp.assets:
            ids.append(store.add_upload(doc["id"], a.filename, a.data, purpose="asset", mime=a.mime)["id"])
        for blk in blocks:
            src = blk["content"].get("src") if blk["type"] == "image" else None
            if isinstance(src, str) and src.startswith("asset:"):
                blk["content"] = {**blk["content"], "src": ids[int(src[6:])]}
        store.insert_blocks(doc["id"], blocks, author=author,
                            note=f"imported from {Path(filename or 'file').name}"[:200])
    except BaseException:
        for uid in ids:  # no half-imported document, and no orphaned files
            try:
                store.delete_upload(uid)
            except Exception:  # noqa: BLE001
                pass
        store.delete_document(doc["id"])
        raise
    return store.get_document(doc["id"]), imp
