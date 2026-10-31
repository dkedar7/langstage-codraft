"""Document templates: preset settings plus a block skeleton.

Built-ins cover the common shapes from the design notes (report, proposal, memo,
paper, one-pager, newsletter). Deployers add their own as ``*.json`` files in the
``[codraft] templates_dir`` directory, each shaped like::

    {"name": "Design doc", "description": "...", "settings": {...},
     "blocks": [{"type": "heading", "content": {"text": "Title", "level": 1}}, ...]}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _h(text: str, level: int = 1, **layout: Any) -> dict:
    return {"type": "heading", "content": {"text": text, "level": level}, "layout": layout}


def _t(md: str, **layout: Any) -> dict:
    return {"type": "text", "content": {"markdown": md}, "layout": layout}


BUILTIN_TEMPLATES: dict[str, dict[str, Any]] = {
    "blank": {
        "name": "Blank", "description": "An empty page.",
        "settings": {}, "blocks": [],
    },
    "report": {
        "name": "Report",
        "description": "Cover page, executive summary, sections, conclusion and references.",
        "settings": {"theme": "classic", "columns": 1},
        "blocks": [
            _h("Report title", 1, role="cover"),
            _t("*Subtitle, author, date*", role="cover"),
            _h("Executive summary", 2),
            _t("Summarize the key findings and recommendations in a few sentences."),
            _h("Background", 2),
            _t("Why this report exists and what question it answers."),
            _h("Findings", 2),
            _t("The evidence, one idea per block."),
            _h("Conclusion", 2),
            _t("What the reader should do or believe now."),
            {"type": "references", "content": {"title": "References"}, "layout": {}},
        ],
    },
    "proposal": {
        "name": "Proposal",
        "description": "Problem, proposed solution, plan, cost and risks.",
        "settings": {"theme": "modern"},
        "blocks": [
            _h("Proposal: <title>", 1),
            {"type": "callout", "content": {"markdown": "**Ask:** what you want approved, by when.", "tone": "info"}, "layout": {}},
            _h("Problem", 2), _t("Describe the problem and who feels it."),
            _h("Proposed solution", 2), _t("What you will build or change."),
            _h("Plan and milestones", 2),
            {"type": "table", "content": {"rows": [["Milestone", "Owner", "Date"], ["", "", ""]], "header": True}, "layout": {}},
            _h("Cost and risks", 2), _t("Budget, risks and how each risk is mitigated."),
        ],
    },
    "memo": {
        "name": "Memo",
        "description": "A one-page strategy or decision memo.",
        "settings": {"theme": "minimal"},
        "blocks": [
            _h("Memo", 1), _t("**To:** \n**From:** \n**Date:** \n**Re:** "),
            {"type": "divider", "content": {}, "layout": {}},
            _t("Bottom line up front: the decision or recommendation."),
            _h("Context", 3), _t("What changed."),
            _h("Recommendation", 3), _t("What to do, and why."),
        ],
    },
    "paper": {
        "name": "Academic paper",
        "description": "Two-column paper with a full-width title and abstract.",
        "settings": {"theme": "academic", "columns": 2, "page_size": "letter",
                     "margins": {"top": 18, "right": 16, "bottom": 18, "left": 16},
                     "brief": {"writing_style": "academic"}},
        "blocks": [
            _h("Paper title", 1, span="full"),
            _t("*Author One, Author Two*", span="full"),
            {"type": "callout", "content": {"markdown": "**Abstract.** One paragraph: problem, method, result, implication.", "tone": "note"}, "layout": {"span": "full"}},
            _h("1 Introduction", 2), _t("Motivation and contributions."),
            _h("2 Related work", 2), _t("How this differs from prior work."),
            _h("3 Method", 2), _t("What you did."),
            _h("4 Results", 2), _t("What you found."),
            _h("5 Conclusion", 2), _t("What it means."),
            {"type": "references", "content": {"title": "References"}, "layout": {}},
        ],
    },
    "one-pager": {
        "name": "Marketing one-pager",
        "description": "Bold headline, value props in columns, call to action.",
        "settings": {"theme": "bold", "columns": 2},
        "blocks": [
            _h("Headline that names the outcome", 1, span="full"),
            _t("One sentence on who it is for and why it matters.", span="full"),
            _h("Benefit one", 3), _t("Proof point."),
            _h("Benefit two", 3), _t("Proof point."),
            {"type": "callout", "content": {"markdown": "**Get started:** the call to action.", "tone": "success"}, "layout": {"span": "full"}},
        ],
    },
    "newsletter": {
        "name": "Newsletter",
        "description": "Three columns with a repeating masthead.",
        "settings": {"theme": "classic", "columns": 3},
        "blocks": [
            {"type": "heading", "content": {"text": "The Newsletter", "level": 1},
             "layout": {"span": "full", "pin": {"page": 1, "top": 0, "column": 0, "span": 3, "height": 56}, "repeat": "all"}},
            _h("Lead story", 2), _t("Lead story text."),
            _h("Second story", 2), _t("Second story text."),
        ],
    },
}

_dir: Path | None = None


def configure(templates_dir: str | Path | None) -> None:
    global _dir
    _dir = Path(templates_dir) if templates_dir else None


def _load(p: Path) -> dict[str, Any]:
    """One deployer template file, or ValueError saying what's wrong with it."""
    try:
        t = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"can't read it: {exc.strerror or exc}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from None
    if not isinstance(t, dict):
        raise ValueError(f"expected a JSON object, got {type(t).__name__}")
    for key, kind in (("settings", dict), ("blocks", list)):
        if key in t and not isinstance(t[key], kind):
            raise ValueError(f"{key!r} must be a JSON {'object' if kind is dict else 'array'}")
    for i, b in enumerate(t.get("blocks", [])):
        if not isinstance(b, dict):
            raise ValueError(f"blocks[{i}] must be a JSON object")
        if not isinstance(b.get("content", {}), dict):
            raise ValueError(f'blocks[{i}].content must be a JSON object, e.g. {{"markdown": "..."}}')
    t.setdefault("name", p.stem)
    t.setdefault("settings", {})
    t.setdefault("blocks", [])
    return t


def _scan() -> tuple[dict[str, dict[str, Any]], dict[str, tuple[Path, str]]]:
    out = dict(BUILTIN_TEMPLATES)
    skipped: dict[str, tuple[Path, str]] = {}
    if _dir and _dir.is_dir():
        for p in sorted(_dir.glob("*.json")):
            try:
                out[p.stem] = _load(p)
            except ValueError as exc:
                skipped[p.stem] = (p, str(exc))
    return out, skipped


def all_templates() -> dict[str, dict[str, Any]]:
    return _scan()[0]


def skipped_templates() -> dict[str, tuple[Path, str]]:
    """Template files in templates_dir that couldn't be used: id -> (path, reason) (gh #27)."""
    return _scan()[1]


def unknown_template_message(template_id: str) -> str:
    """Why a template id doesn't resolve: its file's error if it has one, else the known ids."""
    bad = skipped_templates().get(template_id)
    if bad:
        return f"template {template_id!r} was skipped: {bad[0]}: {bad[1]}"
    return f"unknown template {template_id!r}; available: {', '.join(all_templates())}"


def summaries() -> list[dict]:
    return [{"id": k, "name": v.get("name", k), "description": v.get("description", ""),
             "has_cover": any((b.get("layout") or {}).get("role") == "cover" for b in v.get("blocks", []))}
            for k, v in all_templates().items()]
