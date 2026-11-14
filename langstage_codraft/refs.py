"""References and citations.

A block's ``refs`` are its sources. Each has a ``title``, and may have ``url``, ``authors``,
``year``, ``publisher`` and ``accessed``; these are printed in the reference list. ``note`` is
a private note for the author and the agent and is never printed. ``key`` is the short name
the text cites the source by: ``[@key]`` in Markdown prints as ``[3]``, its number in the list.

The list is numbered in order of first appearance: a source is numbered where the text first
cites it, or where the first block that carries it stands. The frontend
(``frontend/src/render/refs.ts``) and this module implement the same rules, so the editor,
the exports and the agent's tools agree on every number.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

#: The fields of a reference that are printed, in the order the list prints them.
PRINTED = ("authors", "year", "title", "publisher", "url", "accessed")
FIELDS = ("key", "title", "url", "authors", "year", "publisher", "accessed", "note")
_LIMITS = {"key": 60, "title": 500, "url": 2000, "authors": 500, "year": 20, "publisher": 300,
           "accessed": 40, "note": 2000}

_KEY = r"[A-Za-z0-9_][A-Za-z0-9_:.+-]*"
#: ``[@key]`` or ``[@one; @two]``.
CITE = re.compile(rf"\[(@{_KEY}(?:\s*[;,]\s*@{_KEY})*)\]")
_KEY_OK = re.compile(rf"^{_KEY}$")


def _slug(text: str) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def _first_author(authors: str) -> str:
    """The family name of the first author: "Smith, J. and Lee, K." or "Jane Smith, Kim Lee"."""
    first = re.split(r"\s+(?:and|&)\s+|;", authors.strip(), maxsplit=1)[0].strip()
    if "," in first:
        first = first.split(",", 1)[0]
    else:
        words = [w for w in first.split() if not re.fullmatch(r"(?:[A-Z]\.)+|et|al\.?", w)]
        first = words[-1] if words else first
    return _slug(first)


def auto_key(ref: dict) -> str:
    """A key for a source that has none: the first author and the year (smith2020), else the
    first words of the title. The same source always gets the same key."""
    year = re.search(r"\d{4}", str(ref.get("year") or ""))
    name = _first_author(str(ref.get("authors") or ""))
    title = str(ref.get("title") or "")
    site = re.match(r"https?://(?:www\.)?([^/:?#\s]+)", title if title.startswith("http") else "")
    if not name and site:
        # A source known only by its address: the site's name (example.org -> example).
        labels = site.group(1).split(".")
        name = _slug(labels[0]) if len(labels[0]) >= 3 else _slug(site.group(1))
    if not name:
        words = [_slug(w) for w in re.split(r"\s+", title)]
        words = [w for w in words if len(w) > 2 and w not in ("the", "and", "for", "http", "https", "www")]
        name = "".join(words[:2])[:24]
    return (name or "ref") + (year.group(0) if year else "")


def clean_ref(r: Any) -> dict | None:
    """One stored reference, or None when it has no title and no URL. Unknown fields are dropped."""
    if isinstance(r, str):
        r = {"title": r}
    if not isinstance(r, dict):
        return None
    out = {k: str(r.get(k) if r.get(k) is not None else "").strip()[: _LIMITS[k]] for k in FIELDS}
    if isinstance(r.get("authors"), (list, tuple)):
        out["authors"] = ", ".join(str(a).strip() for a in r["authors"] if str(a).strip())[: _LIMITS["authors"]]
    out["title"] = out["title"] or out["url"]
    if not out["title"]:
        return None
    key = out["key"].lstrip("@")
    out["key"] = key if _KEY_OK.match(key) else auto_key(out)
    return out


def clean_refs(refs: list | None) -> list[dict]:
    """A block's stored references. Two sources in one block never share a key."""
    out: list[dict] = []
    used: set[str] = set()
    for r in refs or []:
        c = clean_ref(r)
        if c is None:
            continue
        base, n = c["key"], 2
        while c["key"].lower() in used:
            c["key"] = f"{base}-{n}"
            n += 1
        used.add(c["key"].lower())
        out.append(c)
    return out


def identity(r: dict) -> str:
    """What makes two references the same source: the URL, or the title when there is none."""
    return str(r.get("url") or r.get("title") or "").lower()


def cite_keys(text: str) -> list[str]:
    """The keys cited in a piece of Markdown, in order."""
    out: list[str] = []
    for m in CITE.finditer(text or ""):
        out += [k.strip().lstrip("@") for k in re.split(r"[;,]", m.group(1))]
    return out


def block_texts(block: dict) -> list[str]:
    """The Markdown a block prints, in reading order (where citations can stand)."""
    c = block.get("content") or {}
    t = block.get("type")
    if t in ("text", "callout"):
        return [str(c.get("markdown") or "")]
    if t == "row":
        return [str((cell or {}).get("markdown") or "") if isinstance(cell, dict) else str(cell or "")
                for cell in c.get("cells") or []]
    if t == "table":
        return [str(cell if cell is not None else "") for row in c.get("rows") or [] if isinstance(row, list) for cell in row]
    return []


def compile_refs(blocks: list[dict]) -> list[dict]:
    """Every source in the document, de-duplicated and numbered in order of first appearance.
    Each comes back with ``n`` (its number) and ``block`` (the first block that carries it)."""
    by_id: dict[str, dict] = {}
    key_to_id: dict[str, str] = {}
    for b in blocks:
        for r in b.get("refs") or []:
            ident = identity(r)
            if not ident:
                continue
            by_id.setdefault(ident, {**r, "block": b.get("id")})
            if r.get("key"):
                key_to_id.setdefault(str(r["key"]).lower(), ident)
    order: list[str] = []
    seen: set[str] = set()

    def take(ident: str | None) -> None:
        if ident and ident in by_id and ident not in seen:
            seen.add(ident)
            order.append(ident)

    for b in blocks:
        for text in block_texts(b):
            for key in cite_keys(text):
                take(key_to_id.get(key.lower()))
        for r in b.get("refs") or []:
            take(identity(r))
    return [{**by_id[ident], "n": i + 1} for i, ident in enumerate(order)]


def cite_numbers(refs: list[dict]) -> dict[str, int]:
    """key (lower case) -> number, for the references :func:`compile_refs` returned. Every key
    a duplicate of the source was stored under resolves to the same number."""
    return {str(r["key"]).lower(): r["n"] for r in refs if r.get("key")}


def numbers_for(blocks: list[dict]) -> dict[str, int]:
    """key -> number for a document's blocks, including the keys of duplicate entries."""
    refs = compile_refs(blocks)
    by_ident = {identity(r): r["n"] for r in refs}
    out = cite_numbers(refs)
    for b in blocks:
        for r in b.get("refs") or []:
            if r.get("key") and identity(r) in by_ident:
                out.setdefault(str(r["key"]).lower(), by_ident[identity(r)])
    return out


def replace_cites(text: str, numbers: dict[str, int]) -> str:
    """``[@key]`` -> ``[3]`` (``[@a; @b]`` -> ``[1, 2]``); a key with no source prints ``[?]``."""
    def one(m: re.Match) -> str:
        keys = [k.strip().lstrip("@") for k in re.split(r"[;,]", m.group(1))]
        return "[" + ", ".join(str(numbers.get(k.lower(), "?")) for k in keys) + "]"

    return CITE.sub(one, text or "")


def format_ref(r: dict, *, url: bool = True) -> str:
    """One entry of the reference list as plain text: Authors (Year). Title. Publisher. URL.
    Accessed DATE. The private note is not part of it."""
    authors, year = str(r.get("authors") or "").strip(), str(r.get("year") or "").strip()
    link = str(r.get("url") or "").strip()
    title = str(r.get("title") or "").strip()
    title = "" if title == link else title   # known only by its address: the link alone
    lead = authors + (f" ({year})" if year else "") if authors else ""
    parts = []
    if lead:
        parts.append(lead)
    if title:
        parts.append(title + (f" ({year})" if year and not authors else ""))
    if r.get("publisher"):
        parts.append(str(r["publisher"]).strip())
    text = ". ".join(p.rstrip(".") for p in parts if p)
    text = text + "." if text else ""
    if url and link:
        text = (text + " " if text else "") + link
    if r.get("accessed"):
        text = (text + " " if text else "") + f"Accessed {str(r['accessed']).strip()}."
    return text


_AUTHOR_YEAR = re.compile(r"^(?P<authors>[^()]{2,200}?)\s*\((?P<year>\d{4}[a-z]?)\)[.,:]?\s+(?P<rest>\S.*)$")


def parse_citation(text: str) -> dict:
    """Read "Smith, J. (2020). Title. Publisher." into fields, when the text has that shape.
    Anything else comes back as a title only."""
    text = re.sub(r"\s+", " ", text or "").strip()
    m = _AUTHOR_YEAR.match(text)
    if not m or not re.search(r"[A-Za-z]", m.group("authors")):
        return {"title": text}
    rest = m.group("rest").strip()
    title, _, tail = rest.partition(". ")
    out = {"authors": m.group("authors").strip(" .,"), "year": m.group("year"), "title": title.strip(" .") or rest}
    if tail.strip(" ."):
        out["publisher"] = tail.strip(" .")
    return out
