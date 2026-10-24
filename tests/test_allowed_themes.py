"""0.5.6: new documents honour [codraft] allowed_themes (gh #15)."""
import json
import sqlite3

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from langstage_codraft import themes
from langstage_codraft.cli import main
from langstage_codraft.config import CodraftConfig
from langstage_codraft.server import create_app
from langstage_codraft.store import Store

TOML = """
[codraft]
default_theme = "acme"
allowed_themes = ["acme", "academic"]
data_dir = ".codraft"

[codraft.themes.acme]
label = "Acme"
font_body = "Inter, system-ui, sans-serif"
color_accent = "#d9480f"
"""

TEMPLATES = ["report", "proposal", "memo", "paper", "one-pager", "newsletter"]


@pytest.fixture(autouse=True)
def _reset_themes():
    yield
    themes.configure(None, None)


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "langstage.toml").write_text(TOML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"port": 8799})
    with TestClient(create_app(cfg, None, {"kind": "none"}, store=Store(tmp_path / ".codraft" / "codraft.db"))) as c:
        yield c


def test_builtin_templates_only_get_allowed_themes(client):
    got = {t: client.post("/api/docs", json={"template": t, "title": t}).json()["settings"]["theme"]
           for t in TEMPLATES}
    # paper's academic is allowed, so it keeps it; the rest fall back to the house theme.
    assert got == {"report": "acme", "proposal": "acme", "memo": "acme", "paper": "academic",
                   "one-pager": "acme", "newsletter": "acme"}


def test_post_refuses_a_disallowed_theme_like_patch(client):
    r = client.post("/api/docs", json={"title": "T", "settings": {"theme": "modern"}})
    assert r.status_code == 400 and "unknown theme 'modern'" in r.text
    assert client.post("/api/docs", json={"title": "T", "settings": {"theme": "academic"}}).json()["settings"]["theme"] == "academic"


def test_cli_new_honours_allowed_themes(tmp_path, monkeypatch):
    (tmp_path / "langstage.toml").write_text(TOML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    ids = {}
    for t in ("proposal", "memo", "paper"):
        res = CliRunner().invoke(main, ["new", f"Q3 {t}", "--template", t])
        assert res.exit_code == 0, res.output
        ids[t] = res.output.strip()
    con = sqlite3.connect(tmp_path / ".codraft" / "codraft.db")
    try:
        got = {t: json.loads(con.execute("select settings from documents where id=?", (i,)).fetchone()[0])["theme"]
               for t, i in ids.items()}
    finally:
        con.close()
    assert got == {"proposal": "acme", "memo": "acme", "paper": "academic"}


def test_a_stored_theme_no_longer_offered_renders_in_default_theme():
    themes.configure({"acme": {"label": "Acme", "color_accent": "#d9480f"}}, ["acme", "academic"], "acme")
    assert themes.effective_theme({"theme": "modern"})["label"] == "Acme"  # not Academic, the first built-in left
    assert themes.effective_theme({"theme": "academic"})["label"] == themes.BUILTIN_THEMES["academic"]["label"]


def test_fallback_order_without_a_usable_default():
    themes.configure(None, ["minimal", "modern"], "acme")  # default_theme not offered, no classic
    assert themes.fallback_theme() in ("minimal", "modern")
    themes.configure(None, None, None)
    assert themes.fallback_theme() == "classic"
