"""Codraft configuration: the family's ``HostConfig`` chain plus a ``[codraft]`` table.

Precedence (lowest to highest): defaults < ``langstage.toml`` < ``LANGSTAGE_*`` env <
CLI flags, exactly as on every other LangStage surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from langstage_core import HostConfig


def _env_bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class CodraftConfig(HostConfig):
    port: int = 8060
    title: str = "Codraft"
    model: str | None = None              # e.g. "openrouter:anthropic/claude-haiku-5.5", "openai:gpt-4.1-mini"
    approval: str = "required"            # default approval mode for new documents
    data_dir: Path = Path(".codraft")     # SQLite store, uploads, checkpoints, renders
    templates_dir: Path | None = None
    default_theme: str = "classic"
    demo: bool = False
    theme: str = "auto"                   # app chrome: light | dark | auto (same as langstage web)

    _ENV: ClassVar[dict] = {
        "model": ("LANGSTAGE_CODRAFT_MODEL", str),
        "approval": ("LANGSTAGE_CODRAFT_APPROVAL", str),
        "data_dir": ("LANGSTAGE_CODRAFT_DATA_DIR", Path),
        "templates_dir": ("LANGSTAGE_CODRAFT_TEMPLATES_DIR", Path),
        "default_theme": ("LANGSTAGE_CODRAFT_THEME", str),
        "demo": ("LANGSTAGE_CODRAFT_DEMO", _env_bool),
        "theme": ("LANGSTAGE_THEME", str),
    }
    _TOML: ClassVar[dict] = {
        "model": "codraft.model",
        "approval": "codraft.approval",
        "data_dir": "codraft.data_dir",
        "templates_dir": "codraft.templates_dir",
        "default_theme": "codraft.default_theme",
        "demo": "codraft.demo",
        "theme": "ui.theme",
    }
    _TOML_PATHS: ClassVar[dict] = {"data_dir": "path", "templates_dir": "path"}
    # Read directly from the [codraft] table (themes.py), not config fields: never "unknown".
    _TOML_PASSTHROUGH: ClassVar[tuple[str, ...]] = ("configurable", "codraft.themes", "codraft.allowed_themes")


def codraft_toml_table() -> dict[str, Any]:
    """The raw ``[codraft]`` table (themes and the allowed list live there)."""
    try:
        from langstage_core.host.config import load_toml_config

        data, _ = load_toml_config()
    except Exception:  # noqa: BLE001 - a malformed TOML is reported by describe()
        return {}
    t = data.get("codraft", {})
    return t if isinstance(t, dict) else {}
