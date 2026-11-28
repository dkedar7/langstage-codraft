"""Export file names from a document's title (gh #79).

A download is named after the title, the way Word or Google Docs name one. Latin letters
with accents are spelled without them (``Rapport d'activité`` → ``rapport-d-activite``) and
every other script keeps its letters (``日本の報告``, ``отчёт-за-год``). HTTP headers are
ASCII, so the export route sends both: an ASCII ``filename=`` for old clients and the real
name as RFC 5987 ``filename*=UTF-8''…``.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote, unquote

MAX_STEM = 60
FALLBACK = "document"

# Latin letters NFKD can't take apart into a base letter and marks.
_LATIN = {"ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ø": "o", "Ø": "O",
          "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "ı": "i"}


def _latin_plain(text: str) -> str:
    """Spell accented Latin letters without their marks; leave every other script alone.

    Only a letter that decomposes to an ASCII base plus combining marks loses the marks, so
    ``é`` becomes ``e`` while Cyrillic ``ё`` and Japanese ``が`` (base plus a combining mark
    too) stay as they are.
    """
    out = []
    for ch in unicodedata.normalize("NFC", text):
        if ch in _LATIN:
            out.append(_LATIN[ch])
            continue
        parts = unicodedata.normalize("NFKD", ch)
        if len(parts) > 1 and parts[0].isascii() and all(unicodedata.combining(p) for p in parts[1:]):
            out.append(parts[0])
        else:
            out.append(ch)
    return "".join(out)


def _cut(stem: str) -> str:
    return stem[:MAX_STEM].strip("-")


def _words(text: str) -> str:
    """Letters, digits and marks (a Devanagari vowel sign, an Arabic vowel) kept; every run
    of anything else becomes one ``-``."""
    out, gap = [], False
    for ch in text:
        if unicodedata.category(ch)[0] in "LNM":
            if gap and out:
                out.append("-")
            out.append(ch)
            gap = False
        else:
            gap = True
    return "".join(out)


def export_stem(title: str) -> str:
    """The file name stem for ``title``: lower case, words joined by ``-``, any script."""
    words = _words(_latin_plain(title or "")).lower()
    return _cut(unicodedata.normalize("NFC", words)) or FALLBACK


def ascii_stem(title: str) -> str:
    """The same stem with only ASCII letters and digits, for the plain ``filename=``."""
    words = re.sub(r"[^A-Za-z0-9]+", "-", _latin_plain(title or "")).strip("-").lower()
    return _cut(words) or FALLBACK


def content_disposition(title: str, tail: str) -> str:
    """``attachment`` with the file name ``<stem><tail>`` (``tail`` like ``.pdf``, ``-redline.docx``)."""
    plain = ascii_stem(title) + tail
    real = export_stem(title) + tail
    disp = f'attachment; filename="{plain}"'
    if real != plain:
        disp += f"; filename*=UTF-8''{quote(real, safe='')}"
    return disp


_UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')


def safe_filename(name: str) -> str:
    """``name`` with what a file system can't take (or a path needs) replaced: separators,
    ``: * ? " < > |`` and control characters. Never empty, never ``.`` or ``..``."""
    name = _UNSAFE.sub("-", name).strip(" .")
    return name or FALLBACK


def filename_from_disposition(disp: str) -> str | None:
    """The file name a ``Content-Disposition`` header suggests: ``filename*`` first (RFC 6266),
    then ``filename``. Made safe to write into a directory."""
    m = re.search(r"filename\*\s*=\s*([\w-]+)'[^']*'([^;]+)", disp, re.I)
    if m:
        try:
            return safe_filename(unquote(m.group(2).strip(), encoding=m.group(1), errors="strict"))
        except (LookupError, UnicodeDecodeError):
            pass
    m = re.search(r'filename\s*=\s*"([^"]*)"', disp, re.I) or re.search(r"filename\s*=\s*([^;]+)", disp, re.I)
    return safe_filename(m.group(1).strip()) if m else None
