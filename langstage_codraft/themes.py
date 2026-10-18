"""Design themes: named sets of CSS variables the renderer applies to every block.

Deployers add or override themes in ``langstage.toml``::

    [codraft.themes.acme]
    font_body = "'Source Serif 4', Georgia, serif"
    font_heading = "Inter, system-ui, sans-serif"
    color_accent = "#d9480f"

Unset keys inherit from ``classic``. ``[codraft] themes = ["acme", "modern"]`` limits
which themes authors can pick.
"""
from __future__ import annotations

from typing import Any

THEME_KEYS = (
    "font_body", "font_heading", "font_mono", "font_size", "line_height", "heading_scale",
    "color_text", "color_muted", "color_accent", "color_page", "color_rule", "color_callout",
    "heading_weight", "heading_case", "font_display", "display_weight", "font_label",
    "text_align", "hyphens", "color_figure", "paragraph_gap", "paragraph_indent",
)

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
    return merged


def theme_names() -> list[str]:
    return list(all_themes())
