"""gh #41: a templates_dir template gets the checks a new document's blocks get when it
loads, so `check` can't call it ok while `new`, REST and MCP refuse it. Deployer themes
and custom_css get the same treatment: `check` says what the exports would choke on."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from test_mcp import _text

from langstage_codraft import cli, exporters, themes
from langstage_codraft import templates as tpl
from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.mcp_server import build_mcp_server
from langstage_codraft.server import create_app
from langstage_codraft.store import Store

BROKEN = {
    "quote": [{"type": "quote", "content": {"markdown": "Status"}}],
    "layout-str": [{"type": "text", "layout": "wide"}],
    "variant": [{"type": "heading", "content": {"text": "Hi"}, "layout": {"variant": "nope"}}],
    "field": [{"type": "heading", "content": {"text": 5}}],
    "span": [{"type": "text", "layout": {"span": "half"}}],
    "pin": [{"type": "text", "layout": {"pin": {"page": "two"}}}],
    "refs": [{"type": "text", "refs": "a source"}],
}


@pytest.fixture
def tdir(tmp_path):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "good.json").write_text(json.dumps({"label": "Good", "blocks": [
        {"type": "heading", "content": {"text": "Weekly"}},
        {"content": {"markdown": "no type means text"}},
        {"type": "text", "layout": {"variant": "lead", "span": "full"}, "refs": ["a source"]},
    ]}), encoding="utf-8")
    yield d
    tpl.configure(None)


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_a_template_block_the_store_would_refuse_is_skipped_with_its_index(tdir, name):
    (tdir / f"{name}.json").write_text(json.dumps({"blocks": [{"type": "divider"}, *BROKEN[name]]}),
                                       encoding="utf-8")
    tpl.configure(tdir)
    assert name not in tpl.all_templates()
    _, why = tpl.skipped_templates()[name]
    assert why.startswith("blocks[1]")
    assert f"{name}.json" in tpl.unknown_template_message(name)
    assert "good" in tpl.all_templates()  # the valid one still loads


def test_unknown_type_names_the_block_types(tdir):
    (tdir / "status.json").write_text(json.dumps({"blocks": BROKEN["quote"]}), encoding="utf-8")
    tpl.configure(tdir)
    why = tpl.skipped_templates()["status"][1]
    assert why == ("blocks[0].type 'quote' is not a block type (text, heading, image, video, table, "
                   "chart, callout, divider, references, page-break, spacer)")


def test_a_valid_template_still_creates_its_document(tdir, tmp_path):
    tpl.configure(tdir)
    s = Store(tmp_path / "c.db")
    t = tpl.all_templates()["good"]
    doc = s.create_document("Weekly", t["settings"], t["blocks"])
    assert [b["type"] for b in s.full(doc["id"])["blocks"]] == ["heading", "text", "text"]


def _toml(tmp_path: Path, body: str) -> None:
    (tmp_path / "langstage.toml").write_text(body, encoding="utf-8")


def test_check_warns_and_new_fails_cleanly(tdir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tdir / "status.json").write_text(json.dumps({"blocks": BROKEN["quote"]}), encoding="utf-8")
    _toml(tmp_path, '[codraft]\ntemplates_dir = "templates"\ndata_dir = "d"\n')
    out = CliRunner().invoke(cli.main, ["check"]).output
    assert "[warn] templates:" in out and "status.json skipped: blocks[0].type 'quote'" in out
    res = CliRunner().invoke(cli.main, ["new", "Weekly", "--template", "status"])
    assert res.exit_code == 2 and "Traceback" not in res.output
    assert "template 'status' was skipped" in res.output
    assert CliRunner().invoke(cli.main, ["new", "Weekly", "--template", "good"]).exit_code == 0


def test_rest_refuses_a_skipped_template_with_the_file_at_fault(tdir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tdir / "status.json").write_text(json.dumps({"blocks": BROKEN["quote"]}), encoding="utf-8")
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "templates_dir": tdir},
                                use_toml=False)
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []},
                     store=Store(tmp_path / "data" / "codraft.db"))
    with TestClient(app) as c:
        assert "status" not in {t["id"] for t in c.get("/api/config").json()["templates"]}
        r = c.post("/api/docs", json={"template": "status"})
        assert r.status_code == 400 and "status.json" in r.text and "'quote'" in r.text


async def test_mcp_create_document_says_why(tdir, store):
    (tdir / "status.json").write_text(json.dumps({"blocks": BROKEN["quote"]}), encoding="utf-8")
    tpl.configure(tdir)
    server = build_mcp_server()
    out = _text(await server.call_tool("create_document", {"title": "x", "template": "status"}))
    assert out.startswith("ERROR: template 'status' was skipped") and "'quote'" in out
    # Any other refusal while creating (here: settings no page can use) gets its reason too.
    out = _text(await server.call_tool("create_document", {"title": "x", "columns": 9}))
    assert out.startswith("ERROR:") and "columns" in out


def test_a_bad_deployer_theme_value_is_dropped_and_reported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    try:
        themes.configure({"house": {"label": "House", "font_size": "big", "color_accent": "nope!",
                                    "line_height": 1.4}, "odd": "not a table"}, None, "house")
        issues = themes.theme_issues()
        assert any("themes.house.font_size ignored" in i for i in issues)
        assert any("themes.house.color_accent ignored" in i for i in issues)
        assert any(i.startswith("themes.odd skipped") for i in issues)
        house = themes.all_themes()["house"]
        assert house["label"] == "House" and house["line_height"] == 1.4
        assert isinstance(house["font_size"], (int, float))  # the built-in base value
        s = Store(tmp_path / "c.db")
        doc = s.create_document("T", {"theme": "house"})
        assert exporters.to_docx(s, doc["id"], {}, [])[:2] == b"PK"  # float("big") used to raise
    finally:
        themes.configure(None, None)


def test_check_reports_bad_theme_values_and_a_missing_css_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _toml(tmp_path, '[codraft]\ndata_dir = "d"\n[codraft.themes.house]\nfont_size = "big"\n'
                    '[ui]\ncustom_css = "house.css"\n')
    try:
        out = CliRunner().invoke(cli.main, ["check"]).output
    finally:
        themes.configure(None, None)
    assert "[warn] themes: themes.house.font_size ignored: font_size must be a number" in out
    assert "[warn] custom-css: custom_css file not found:" in out and "house.css" in out
    (tmp_path / "house.css").write_text("body{}", encoding="utf-8")
    try:
        assert "custom-css" not in CliRunner().invoke(cli.main, ["check"]).output
    finally:
        themes.configure(None, None)
