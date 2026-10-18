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
    default_page_size: str = ""          # letter | a4 | legal | a5 | slide (new documents)
    default_theme: str = "classic"
    demo: bool = False
    theme: str = "auto"                   # app chrome: light | dark | auto (same as langstage web)
    # App branding (same keys as langstage web, so one langstage.toml brands both stages).
    subtitle: str = ""
    agent_name: str = "Co-author"
    welcome_message: str = ""
    icon_url: str = ""
    custom_css: str = ""                  # inline CSS, or a path to a .css file
    # Hosting inside another app.
    embedded: bool = False                # no Codraft header: the host page provides navigation
    base_path: str = ""                   # public URL prefix when served behind a proxy (e.g. /write)
    renderer_url: str = ""                # how the server reaches itself for exports (default http://host:port)
    proxy_secret: str = ""                # if set, every non-local request must carry X-Codraft-Proxy-Secret
    user_header: str = ""                 # trusted header naming the signed-in user (e.g. X-User-Email)
    auth_username: str = "admin"          # HTTP basic auth (inert unless auth_password is set)
    auth_password: str = ""

    _ENV: ClassVar[dict] = {
        "model": ("LANGSTAGE_CODRAFT_MODEL", str),
        "approval": ("LANGSTAGE_CODRAFT_APPROVAL", str),
        "data_dir": ("LANGSTAGE_CODRAFT_DATA_DIR", Path),
        "templates_dir": ("LANGSTAGE_CODRAFT_TEMPLATES_DIR", Path),
        "default_theme": ("LANGSTAGE_CODRAFT_THEME", str),
        "demo": ("LANGSTAGE_CODRAFT_DEMO", _env_bool),
        "theme": ("LANGSTAGE_THEME", str),
        "default_page_size": ("LANGSTAGE_CODRAFT_DEFAULT_PAGE_SIZE", str),
        "subtitle": ("LANGSTAGE_SUBTITLE", str),
        "agent_name": ("LANGSTAGE_AGENT_NAME", str),
        "welcome_message": ("LANGSTAGE_WELCOME_MESSAGE", str),
        "icon_url": ("LANGSTAGE_ICON_URL", str),
        "custom_css": ("LANGSTAGE_CUSTOM_CSS", str),
        "embedded": ("LANGSTAGE_CODRAFT_EMBEDDED", _env_bool),
        "base_path": ("LANGSTAGE_CODRAFT_BASE_PATH", str),
        "renderer_url": ("LANGSTAGE_CODRAFT_RENDERER_URL", str),
        "proxy_secret": ("LANGSTAGE_CODRAFT_PROXY_SECRET", str),
        "user_header": ("LANGSTAGE_CODRAFT_USER_HEADER", str),
        "auth_username": ("LANGSTAGE_AUTH_USERNAME", str),
        "auth_password": ("LANGSTAGE_AUTH_PASSWORD", str),
    }
    _TOML: ClassVar[dict] = {
        "model": "codraft.model",
        "approval": "codraft.approval",
        "data_dir": "codraft.data_dir",
        "templates_dir": "codraft.templates_dir",
        "default_theme": "codraft.default_theme",
        "demo": "codraft.demo",
        "theme": "ui.theme",
        "default_page_size": "codraft.default_page_size",
        "subtitle": "ui.subtitle",
        "agent_name": "ui.agent_name",
        "welcome_message": "ui.welcome_message",
        "icon_url": "ui.icon_url",
        "custom_css": "ui.custom_css",
        "embedded": "ui.embedded",
        "base_path": "ui.base_path",
        "renderer_url": "codraft.renderer_url",
        "proxy_secret": "codraft.proxy_secret",
        "user_header": "codraft.user_header",
        "auth_username": "auth.username",
        "auth_password": "auth.password",
    }
    _TOML_PATHS: ClassVar[dict] = {"data_dir": "path", "templates_dir": "path"}
    # Read directly from the [codraft] table (themes.py), not config fields: never "unknown".
    _TOML_PASSTHROUGH: ClassVar[tuple[str, ...]] = (
        "configurable", "codraft.themes", "codraft.allowed_themes",
        "ui.colors", "ui.colors_dark", "ui.starter_prompts", "ui.font_family", "ui.font_url",
        "ui.features",
    )
    _SECRETS: ClassVar[tuple[str, ...]] = ("proxy_secret", "auth_password")

    def describe(self, *args: Any, **kwargs: Any) -> str:  # type: ignore[override]
        """The family's config report, with secrets masked."""
        import re

        text = super().describe(*args, **kwargs)
        for name in self._SECRETS:
            if getattr(self, name, ""):
                text = re.sub(rf"(?m)^(\s*{name}\s*=\s*)\S.*?(\s{{2,}}\[)", r"\1********\2", text)
        return text


def ui_toml_table() -> dict[str, Any]:
    """The raw ``[ui]`` table: colours, fonts, starter prompts and feature switches."""
    try:
        from langstage_core.host.config import load_toml_config

        data, _ = load_toml_config()
    except Exception:  # noqa: BLE001
        return {}
    t = data.get("ui", {})
    return t if isinstance(t, dict) else {}


def codraft_toml_table() -> dict[str, Any]:
    """The raw ``[codraft]`` table (themes and the allowed list live there)."""
    try:
        from langstage_core.host.config import load_toml_config

        data, _ = load_toml_config()
    except Exception:  # noqa: BLE001 - a malformed TOML is reported by describe()
        return {}
    t = data.get("codraft", {})
    return t if isinstance(t, dict) else {}
