"""Codraft configuration: the family's ``HostConfig`` chain plus a ``[codraft]`` table.

Precedence (lowest to highest): defaults < ``langstage.toml`` < ``LANGSTAGE_*`` env <
CLI flags, exactly as on every other LangStage surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from langstage_core import HostConfig


def _env_bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def _csv(v: str) -> list[str]:
    return [s.strip() for s in v.split(",") if s.strip()]


def _page_size_name(v: str) -> str:
    """``default_page_size``: a named size, case-insensitive ("A4" means a4); "" = letter (gh #30)."""
    from .pages import PAGE_SIZES_MM

    name = str(v).strip().lower()
    if name and name not in PAGE_SIZES_MM:
        raise ValueError(f"must be one of {', '.join(PAGE_SIZES_MM)}")
    return name


def _approval_mode(v: str) -> str:
    mode = str(v).strip().lower()
    if mode not in ("required", "auto"):
        raise ValueError("must be required or auto")
    return mode


def is_css_path(value: str) -> bool:
    """``custom_css`` holds inline CSS or a path to a .css file; this says which."""
    v = (value or "").strip()
    return v.endswith(".css") and "\n" not in v and "{" not in v


@dataclass
class CodraftConfig(HostConfig):
    port: int = 8060
    title: str = "Codraft"
    model: str | None = None              # e.g. "openrouter:anthropic/claude-haiku-5.5", "openai:gpt-4.1-mini"
    approval: str = "required"            # default approval mode for new documents
    data_dir: Path = Path(".codraft")     # SQLite store, uploads, checkpoints, renders
    templates_dir: Path | None = None
    default_page_size: str = ""          # letter | a4 | legal | a5 | slide (new documents; "" = letter)
    default_theme: str = "classic"
    web_fonts: bool = True                # load non-bundled Google Fonts families (editor and export)
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
    # Extra Host names a loopback-bound `run` answers to (DNS rebinding guard); "*" disables.
    allowed_hosts: list[str] = field(default_factory=list)

    _ENV: ClassVar[dict] = {
        "model": ("LANGSTAGE_CODRAFT_MODEL", str),
        "approval": ("LANGSTAGE_CODRAFT_APPROVAL", str),
        "data_dir": ("LANGSTAGE_CODRAFT_DATA_DIR", Path),
        "templates_dir": ("LANGSTAGE_CODRAFT_TEMPLATES_DIR", Path),
        "default_theme": ("LANGSTAGE_CODRAFT_THEME", str),
        "demo": ("LANGSTAGE_CODRAFT_DEMO", _env_bool),
        "theme": ("LANGSTAGE_THEME", str),
        "default_page_size": ("LANGSTAGE_CODRAFT_DEFAULT_PAGE_SIZE", str),
        "web_fonts": ("LANGSTAGE_CODRAFT_WEB_FONTS", _env_bool),
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
        "allowed_hosts": ("LANGSTAGE_ALLOWED_HOSTS", _csv),
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
        "web_fonts": "codraft.web_fonts",
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
        "allowed_hosts": "server.allowed_hosts",
    }
    _TOML_PATHS: ClassVar[dict] = {"data_dir": "path", "templates_dir": "path"}
    # A bad value degrades to the layer beneath it (else the default) with a note, never a
    # setting --show-config advertises but no document can use (gh #30).
    _VALIDATORS: ClassVar[dict] = {"default_page_size": _page_size_name, "approval": _approval_mode}
    # Read directly from the [codraft] table (themes.py), not config fields: never "unknown".
    _TOML_PASSTHROUGH: ClassVar[tuple[str, ...]] = (
        "configurable", "codraft.themes", "codraft.allowed_themes", "codraft.doc_defaults",
        "ui.colors", "ui.colors_dark", "ui.starter_prompts", "ui.font_family", "ui.font_url",
        "ui.features",
    )
    _SECRETS: ClassVar[tuple[str, ...]] = ("proxy_secret", "auth_password")

    @classmethod
    def resolve(cls, **kwargs: Any) -> CodraftConfig:  # type: ignore[override]
        obj = super().resolve(**kwargs)
        # A relative custom_css path from a langstage.toml resolves against that toml, like
        # data_dir and templates_dir, so branding survives a run from a subdirectory (gh #6).
        # Not a _TOML_PATHS entry: the value can be inline CSS too.
        if obj.sources.get("custom_css", "").startswith("toml") and is_css_path(obj.custom_css):
            files = getattr(obj, "_toml_files", [])  # (path, data), global then project
            src = next((p for p, d in reversed(files)
                        if isinstance(d.get("ui"), dict) and d["ui"].get("custom_css") is not None), None)
            path = Path(obj.custom_css.strip()).expanduser()
            if src is not None and not path.is_absolute():
                obj.custom_css = str(Path(src).parent / path)
        return obj  # type: ignore[return-value]

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
