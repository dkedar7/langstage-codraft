"""Design history: what a document looked like before each change to its look (gh #54).

Content has a version tree per block. The look (document settings, typography, cover,
header and footer, block layouts, reserved areas) is the agent's to change without
approval, so it gets a history instead: every change is logged with the state *before*
it, by whom, and a one-line summary. An agent turn's many style calls are one step, and
so is one drag of a colour picker. Any step can be undone, or the document put back to how
it looked before a step; the restore is itself a step, so nothing is overwritten.

This module is the pure part: what counts as design, how two looks differ and how to say
so in a line. The log itself lives in the store (``Store.design_history``,
``Store.restore_design``), behind the same lock and transaction as the change it records.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

# Settings that are not the document's look: who approves, the brief, the page count the
# author asked for. A design restore never touches them.
NON_DESIGN = ("approval", "brief", "min_pages")

# Steps kept per document; older ones are dropped (the look before them can't be restored).
CAP = 200
# An author's changes this close together are one step (a slider, a colour picker).
AUTHOR_FOLD_S = 10.0
# An agent's changes with no turn to group them by (an MCP client): one step per burst.
AGENT_FOLD_S = 120.0

_MISSING = object()

_KEYS = {
    "columns": "columns", "theme": "theme", "page_size": "page size", "orientation": "orientation",
    "mode": "mode", "margins": "margins", "column_gap": "column gap", "block_gap": "block spacing",
    "cover": "cover", "back": "back page", "header": "header", "footer": "footer", "backfill": "back-fill",
    "page_number_start": "page numbering", "number_cover_pages": "page numbering",
    "unnumbered_pages": "page numbering", "page_w_mm": "page size", "page_h_mm": "page size",
}
_STYLE = {
    "font_body": "body font", "font_heading": "heading font", "font_display": "title font",
    "font_label": "label font", "font_mono": "code font", "font_size": "body size", "line_height": "line height",
    "heading_scale": "heading scale", "heading_weight": "heading weight", "heading_case": "heading case",
    "color_text": "text colour", "color_heading": "heading colour", "color_muted": "muted colour",
    "color_accent": "accent colour", "color_page": "page colour", "color_rule": "rule colour",
    "color_callout": "callout colour", "color_figure": "figure colour", "text_align": "alignment",
    "paragraph_gap": "paragraph spacing", "paragraph_indent": "paragraph indent", "hyphens": "hyphenation",
}
# Shown with their values ("2 → 3 columns"); the rest are named only ("margins").
_VALUED = {"columns", "theme", "page_size", "orientation", "mode", "backfill"}


def design_of(settings: dict | None) -> dict:
    """The part of a document's settings that is its look (a deep copy)."""
    return {k: deepcopy(v) for k, v in (settings or {}).items() if k not in NON_DESIGN}


def _show(key: str, v: Any) -> str:
    if v is _MISSING or v is None:
        return "theme's" if key in _STYLE else "none"
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, str) and key.startswith("font_"):
        first = v.split(",")[0].strip().strip("'\"")
        return first or v
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def changed_paths(before: dict | None, after: dict | None) -> list[tuple[str, ...]]:
    """The settings that differ between two looks, as key paths: ``("columns",)``,
    ``("style", "font_body")``. A dict-valued setting is compared one level down, so two
    people changing different fonts don't count as changing the same thing."""
    a, b = before or {}, after or {}
    out: list[tuple[str, ...]] = []
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k, _MISSING), b.get(k, _MISSING)
        if va == vb:
            continue
        if isinstance(va, dict) and isinstance(vb, dict):
            out += [(k, s) for s in sorted(set(va) | set(vb)) if va.get(s, _MISSING) != vb.get(s, _MISSING)]
        else:
            out.append((k,))
    return out


def get_path(design: dict, path: tuple[str, ...]) -> Any:
    cur: Any = design
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return _MISSING
        cur = cur[p]
    return cur


def set_path(design: dict, path: tuple[str, ...], value: Any) -> None:
    """Set (or, for a missing value, remove) one setting in a look, in place."""
    cur = design
    for p in path[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    if value is _MISSING:
        cur.pop(path[-1], None)
    else:
        cur[path[-1]] = deepcopy(value)


def summarize(before: dict | None, after: dict | None, *, layouts: int = 0, areas_added: int = 0,
              areas_removed: int = 0, limit: int = 4) -> str:
    """One line for a step: ``body font Inter → Lato, 2 → 3 columns, 14 block layouts``."""
    parts: list[str] = []
    seen: set[str] = set()
    if before is not None and after is not None:
        for path in changed_paths(before, after):
            key = path[-1] if path[0] == "style" else path[0]
            label = _STYLE.get(key) if path[0] == "style" else _KEYS.get(key, key.replace("_", " "))
            if label is None:
                label = key.replace("_", " ")
            va, vb = get_path(before, path), get_path(after, path)
            if path[0] == "style":
                text = (f"{label} reset" if vb is _MISSING or vb is None
                        else f"{label} {_show(key, va)} → {_show(key, vb)}" if va is not _MISSING and va is not None
                        else f"{label} {_show(key, vb)}")
            elif key == "columns":
                text = f"{_show(key, va)} → {_show(key, vb)} columns"
            elif key in _VALUED and len(path) == 1:
                text = f"{label} {_show(key, va)} → {_show(key, vb)}"
            else:
                text = label
            if text not in seen:
                seen.add(text)
                parts.append(text)
    more = len(parts) - limit
    if more > 0:
        parts = parts[:limit] + [f"{more} more setting{'s' if more != 1 else ''}"]
    if layouts:
        parts.append(f"{layouts} block layout{'s' if layouts != 1 else ''}")
    if areas_added:
        parts.append(f"{areas_added} reserved area{'s' if areas_added != 1 else ''} added")
    if areas_removed:
        parts.append(f"{areas_removed} reserved area{'s' if areas_removed != 1 else ''} removed")
    return ", ".join(parts) or "no visible change"


def age(seconds: float) -> str:
    """``just now``, ``3 min ago``, ``2 h ago``, ``4 days ago``."""
    s = max(0, int(seconds))
    if s < 45:
        return "just now"
    if s < 3600:
        return f"{max(1, round(s / 60))} min ago"
    if s < 86400:
        return f"{round(s / 3600)} h ago"
    d = round(s / 86400)
    return f"{d} day{'s' if d != 1 else ''} ago"


def who(by: str, kind: str, agent_name: str = "Co-author") -> str:
    """The name a step is shown under."""
    if kind == "agent":
        return agent_name
    by = re.sub(r"\s+", " ", by or "").strip()
    return by if by and by.lower() not in ("author", "human", "user") else "You"
