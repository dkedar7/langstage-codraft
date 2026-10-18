"""Design themes: named sets of CSS variables the renderer applies to every block.

Deployers add or override themes in ``langstage.toml``::

    [codraft.themes.acme]
    font_body = "'Source Serif 4', Georgia, serif"
    font_heading = "Inter, system-ui, sans-serif"
    color_accent = "#d9480f"

Unset keys inherit from ``classic``. ``[codraft] themes = ["acme", "modern"]`` limits
which themes authors can pick.

A theme can also style the cover and back pages (``cover_background``, ``cover_text``,
``cover_image``, ``cover_align``). A document overrides any of these for itself in
``settings.style`` and ``settings.cover`` (the Design tab), and authors can save a
document's look as a new theme, stored with the documents.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# Typography and colour: what a document may override in settings.style.
STYLE_KEYS = (
    "font_body", "font_heading", "font_mono", "font_size", "line_height", "heading_scale",
    "color_text", "color_muted", "color_accent", "color_page", "color_rule", "color_callout",
    "heading_weight", "heading_case", "font_display", "display_weight", "font_label",
    "text_align", "hyphens", "color_figure", "paragraph_gap", "paragraph_indent",
    "color_heading",
)
COVER_KEYS = ("cover_background", "cover_text", "cover_image", "cover_align")
THEME_KEYS = STYLE_KEYS + COVER_KEYS

_NUMBERS = {
    "font_size": (6, 48), "line_height": (0.9, 3.0), "heading_scale": (1.0, 2.0),
    "heading_weight": (100, 900), "display_weight": (100, 900),
    "paragraph_gap": (0, 3), "paragraph_indent": (0, 6),
}
_CHOICES = {
    "heading_case": ("none", "uppercase", "capitalize", "lowercase"),
    "text_align": ("left", "justify", "center", "right"),
    "hyphens": ("manual", "auto", "none"),
    "cover_text": ("auto", "light", "dark"),
    "cover_align": ("top", "center", "bottom"),
}
_COLOR = re.compile(r"^(#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})"
                    r"|(?:rgb|rgba|hsl|hsla)\([0-9.,%\s/]+\)|[a-zA-Z]{3,24})$")
_FONT = re.compile(r"^[^;{}<>\\\n]{1,200}$")
_IMAGE = re.compile(r"^(upl_[a-z0-9]+|https?://\S+|data:image/[a-z+.-]+;base64,[A-Za-z0-9+/=]+)$", re.I)


def is_color(v: Any) -> bool:
    return isinstance(v, str) and bool(_COLOR.match(v.strip()))


def is_image_ref(v: Any) -> bool:
    return isinstance(v, str) and len(v) <= 1_500_000 and bool(_IMAGE.match(v.strip()))


def clean_values(values: dict[str, Any] | None, keys: tuple[str, ...] = THEME_KEYS) -> dict[str, Any]:
    """Validate theme values (a document's overrides or a theme being saved). None drops a
    key; anything malformed raises ValueError naming the key."""
    out: dict[str, Any] = {}
    for k, v in (values or {}).items():
        if v is None or v == "":
            continue
        if k not in keys:
            raise ValueError(f"unknown style key {k!r}; options: {', '.join(keys)}")
        if k in _NUMBERS:
            lo, hi = _NUMBERS[k]
            try:
                n = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number") from None
            if not lo <= n <= hi:
                raise ValueError(f"{k} must be between {lo} and {hi}")
            out[k] = int(n) if k.endswith("weight") else round(n, 3)
        elif k in _CHOICES:
            if v not in _CHOICES[k]:
                raise ValueError(f"{k} must be one of {', '.join(_CHOICES[k])}")
            out[k] = v
        elif k.startswith("color_") or k == "cover_background":
            if not is_color(v):
                raise ValueError(f"{k} must be a colour like #1a4d8f")
            out[k] = v.strip()
        elif k.startswith("font_"):
            if not isinstance(v, str) or not _FONT.match(v):
                raise ValueError(f"{k} must be a CSS font list like \"'Lato', sans-serif\"")
            out[k] = v.strip()
        elif k == "cover_image":
            if not is_image_ref(v):
                raise ValueError("cover_image must be an upload id, an http(s) URL or a data: image")
            out[k] = v.strip()
    return out

BUILTIN_THEMES: dict[str, dict[str, Any]] = {
    "classic": {
        "label": "Classic",
        "font_body": "'Source Serif 4', Georgia, 'Times New Roman', serif",
        "font_heading": "'Source Serif 4', Georgia, serif",
        "font_mono": "'JetBrains Mono', Consolas, monospace",
        "font_size": 15, "line_height": 1.55, "heading_scale": 1.25,
        "color_text": "#1d1d1f", "color_muted": "#5f6368", "color_accent": "#1a4d8f",
        "color_page": "#ffffff", "color_rule": "#d6d6d6", "color_callout": "#eef3fa",
        "heading_weight": 700, "heading_case": "none",
        "font_display": "Inter, system-ui, sans-serif", "display_weight": 300,
        "font_label": "'Roboto Condensed', 'Arial Narrow', sans-serif",
        "text_align": "left", "hyphens": "manual", "color_figure": "#eef0f2",
    },
    "journal": {
        "label": "Journal (magazine feature)",
        "font_body": "'Source Serif 4', 'Times New Roman', serif",
        "font_heading": "'Roboto Condensed', 'Arial Narrow', sans-serif",
        "font_display": "Inter, 'Helvetica Neue', Arial, sans-serif", "display_weight": 300,
        "font_label": "'Roboto Condensed', 'Arial Narrow', sans-serif",
        "font_size": 13, "line_height": 1.42, "heading_scale": 1.12,
        "color_text": "#1a1a1a", "color_muted": "#444444", "color_accent": "#2b8fd0",
        "color_rule": "#9a9a9a", "color_callout": "#eceeef", "color_figure": "#eceeef",
        "heading_weight": 700, "heading_case": "uppercase",
        "text_align": "justify", "hyphens": "auto", "paragraph_gap": 0, "paragraph_indent": 1.1,
    },
    "modern": {
        "label": "Modern",
        "font_body": "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif",
        "font_heading": "Inter, system-ui, sans-serif",
        "font_size": 14, "line_height": 1.6, "heading_scale": 1.3,
        "color_text": "#18181b", "color_muted": "#71717a", "color_accent": "#4f46e5",
        "color_rule": "#e4e4e7", "color_callout": "#eef2ff", "heading_weight": 750,
    },
    "academic": {
        "label": "Academic",
        "font_body": "'Source Serif 4', 'Times New Roman', serif",
        "font_heading": "'Source Serif 4', 'Times New Roman', serif",
        "font_size": 13, "line_height": 1.45, "heading_scale": 1.15,
        "color_text": "#000000", "color_muted": "#444444", "color_accent": "#000000",
        "color_rule": "#999999", "color_callout": "#f4f4f4", "heading_weight": 700,
        "heading_case": "none",
    },
    "minimal": {
        "label": "Minimal",
        "font_body": "Inter, system-ui, sans-serif",
        "font_heading": "Inter, system-ui, sans-serif",
        "font_size": 14, "line_height": 1.7, "heading_scale": 1.2,
        "color_text": "#222222", "color_muted": "#888888", "color_accent": "#222222",
        "color_rule": "#eeeeee", "color_callout": "#fafafa", "heading_weight": 600,
        "heading_case": "none",
    },
    "bold": {
        "label": "Bold (marketing)",
        "font_body": "Inter, system-ui, sans-serif",
        "font_heading": "Inter, system-ui, sans-serif",
        "font_size": 15, "line_height": 1.55, "heading_scale": 1.45,
        "color_text": "#0b1021", "color_muted": "#4b5563", "color_accent": "#e8590c",
        "color_rule": "#fde2cf", "color_callout": "#fff4e6", "heading_weight": 800,
        "heading_case": "none",
    },
}

_custom: dict[str, dict[str, Any]] = {}
_allowed: list[str] | None = None
# Themes authors saved from a document ("Save as theme"); the server installs the store's
# list here, so they survive restarts and show up for every document.
_saved: Callable[[], dict[str, dict[str, Any]]] | None = None


def set_saved_provider(fn: Callable[[], dict[str, dict[str, Any]]] | None) -> None:
    global _saved
    _saved = fn


def configure(custom: dict[str, dict[str, Any]] | None, allowed: list[str] | None) -> None:
    """Install deployer themes (from ``[codraft.themes.*]``) and the allow-list."""
    global _custom, _allowed
    _custom = {k: dict(v) for k, v in (custom or {}).items() if isinstance(v, dict)}
    _allowed = list(allowed) if allowed else None


def all_themes() -> dict[str, dict[str, Any]]:
    base = BUILTIN_THEMES["classic"]
    merged: dict[str, dict[str, Any]] = {}
    for name, t in {**BUILTIN_THEMES, **_custom}.items():
        parent = BUILTIN_THEMES.get(name, base)
        merged[name] = {**base, **parent, **t}
        merged[name].setdefault("label", name.replace("_", " ").title())
    if _allowed:
        merged = {k: v for k, v in merged.items() if k in _allowed} or {"classic": merged["classic"]}
    for name, t in (_saved() if _saved else {}).items():
        if name not in merged:
            merged[name] = {**base, **t, "saved": True}
    return merged


def effective_theme(settings: dict[str, Any]) -> dict[str, Any]:
    """The document's theme with its own overrides (settings.style) on top."""
    every = all_themes()
    base = every.get(settings.get("theme") or "") or next(iter(every.values()))
    return {**base, **{k: v for k, v in (settings.get("style") or {}).items() if v is not None}}


def theme_names() -> list[str]:
    return list(all_themes())
