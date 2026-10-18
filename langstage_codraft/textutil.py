"""Text helpers: upload extraction, keyword search and word budgets."""
from __future__ import annotations

import io
import re
from pathlib import Path

# Rough typographic constants for word budgets (px at 96 dpi).
_AVG_CHARS_PER_WORD = 6.0  # 5 letters + a space


def extract_text(filename: str, data: bytes) -> str:
    """Plain text of an uploaded file; empty string for formats we can't read."""
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            return "\n\n".join((p.extract_text() or "") for p in reader.pages).strip()
        if ext == ".docx":
            import docx

            d = docx.Document(io.BytesIO(data))
            parts = []
            for p in d.paragraphs:
                style = (p.style.name or "").lower() if p.style is not None else ""
                if style.startswith("heading"):
                    level = re.findall(r"\d", style)
                    parts.append("#" * int(level[0] if level else 1) + " " + p.text)
                else:
                    parts.append(p.text)
            return "\n\n".join(x for x in parts if x.strip())
        if ext in (".html", ".htm"):
            html = data.decode("utf-8", "replace")
            html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
            html = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>", "\n", html)
            return re.sub(r"\n{3,}", "\n\n", re.sub(r"<[^>]+>", "", html)).strip()
        if ext in (".md", ".txt", ".csv", ".json", ".tsv", ".rst", ".tex", ".yaml", ".yml", ".py"):
            return data.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - a bad upload must not break the request
        return f"[could not extract text: {type(exc).__name__}: {exc}]"
    return ""


def chunks(text: str, size: int = 1200, overlap: int = 150) -> list[tuple[int, str]]:
    """(offset, chunk) windows over text, split on paragraph boundaries when possible."""
    out: list[tuple[int, str]] = []
    i = 0
    n = len(text)
    while i < n:
        end = min(n, i + size)
        if end < n:
            para = text.rfind("\n\n", i + size // 2, end)
            if para != -1:
                end = para
        out.append((i, text[i:end]))
        if end >= n:
            break
        i = max(end - overlap, i + 1)
    return out


def score(query: str, text: str) -> float:
    """Simple keyword overlap score (no embeddings; deterministic and dependency-free)."""
    terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2]
    if not terms:
        return 0.0
    low = text.lower()
    s = 0.0
    for t in set(terms):
        c = low.count(t)
        if c:
            s += 1.0 + min(c, 5) * 0.2
    if query.lower().strip() in low:
        s += 2.0
    return s / len(set(terms))


def excerpt(text: str, n: int = 100) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def word_budget(width_px: float, height_px: float, font_px: float = 15.0, line_height: float = 1.55) -> int:
    """Approximate words that fit a box of text at the theme's body size."""
    if width_px <= 0 or height_px <= 0:
        return 0
    chars_per_line = width_px / (font_px * 0.5)
    lines = height_px / (font_px * line_height)
    return max(0, int(chars_per_line * lines / _AVG_CHARS_PER_WORD * 0.9))
