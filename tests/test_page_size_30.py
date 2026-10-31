"""gh #30: a setting the renderers would ignore is refused, never stored and reported as applied."""
import json

import pytest
from fastapi.testclient import TestClient
from test_mcp import _text

from langstage_codraft import templates as tpl
from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.mcp_server import build_mcp_server
from langstage_codraft.server import create_app, house_defaults, house_issues
from langstage_codraft.store import Invalid, Store
from langstage_codraft.tools import get_document, set_document_style


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []},
                     store=Store(tmp_path / "data" / "codraft.db"))
    with TestClient(app) as c:
        yield c


def _patch(client, did, settings):
    return client.patch(f"/api/docs/{did}", json={"settings": settings})


def test_rest_refuses_unknown_page_size_and_lists_the_sizes(client):
    did = client.post("/api/docs", json={}).json()["id"]
    r = _patch(client, did, {"page_size": "bogus"})
    assert r.status_code == 400
    assert "letter, a4, legal, a5, slide, custom" in r.json()["detail"]
    assert client.get(f"/api/docs/{did}").json()["settings"]["page_size"] == "letter"
    # The way people write it: "A4" is a4, stored as the name every renderer knows.
    assert _patch(client, did, {"page_size": " A4 "}).json()["settings"]["page_size"] == "a4"


@pytest.mark.parametrize("settings", [
    {"margins": {"top": "abc"}}, {"margins": {"top": -5}}, {"margins": {"top": 200}}, {"margins": "x"},
    {"margins": {"top": 10, "inner": 4}}, {"block_gap": "x"}, {"column_gap": -1}, {"column_gap": True},
    {"page_number_start": "x"}, {"page_number_start": 1.5}, {"number_cover_pages": "maybe"},
    {"min_pages": 0}, {"min_pages": "x"}, {"brief": "x"}, {"brief": {"audience": 5}},
    {"cover": "x"}, {"footer": "x"}, {"page_w_mm": 300}, {"columns": "x"},
])
def test_rest_refuses_values_the_renderers_would_ignore(client, settings):
    did = client.post("/api/docs", json={}).json()["id"]
    before = client.get(f"/api/docs/{did}").json()["settings"]
    r = _patch(client, did, settings)
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith("settings.")
    assert client.get(f"/api/docs/{did}").json()["settings"] == before


def test_valid_values_still_apply(client):
    did = client.post("/api/docs", json={}).json()["id"]
    s = _patch(client, did, {"margins": {"top": 12.5}, "block_gap": 0, "column_gap": 30, "page_number_start": 0,
                             "number_cover_pages": True, "min_pages": 3, "columns": "2",
                             "brief": {"audience": "board"}}).json()["settings"]
    assert s["margins"] == {"top": 12.5, "right": 20, "bottom": 20, "left": 20}
    assert (s["block_gap"], s["column_gap"], s["page_number_start"], s["min_pages"], s["columns"]) == (0, 30, 0, 3, 2)
    s = _patch(client, did, {"page_size": "custom", "page_w_mm": 297, "page_h_mm": 420}).json()["settings"]
    assert (s["page_size"], s["page_w_mm"]) == ("custom", 297)
    assert _patch(client, did, {"page_w_mm": 300}).json()["settings"]["page_w_mm"] == 300  # still custom
    assert _patch(client, did, {"page_size": "letter"}).json()["settings"]["page_size"] == "letter"


def test_a_document_saved_with_an_odd_value_can_still_be_edited(store):
    doc = store.create_document("Old")
    legacy = {**doc["settings"], "page_size": "a3", "margins": {"top": 100, "right": 20, "bottom": 20, "left": 20}}
    store._db.execute("UPDATE documents SET settings = ? WHERE id = ?", (json.dumps(legacy), doc["id"]))
    store._db.commit()
    s = store.update_document(doc["id"], title="Renamed", settings={"columns": 2})["settings"]
    assert s["columns"] == 2 and s["page_size"] == "a3"  # untouched fields aren't re-judged
    with pytest.raises(Invalid):
        store.update_document(doc["id"], settings={"page_size": "a3"})


def test_agent_tool_refuses_a3_and_says_how_to_get_it(store, cfg_for):
    did = store.create_document("Brief")["id"]
    cfg = cfg_for(did)
    out = set_document_style.invoke({"page_size": "A3"}, config=cfg)
    assert out.startswith("ERROR") and "page_w_mm" in out
    assert store.get_document(did)["settings"]["page_size"] == "letter"
    assert "a4 portrait" in set_document_style.invoke({"page_size": "A4"}, config=cfg)
    out = set_document_style.invoke({"page_w_mm": 297, "page_h_mm": 420}, config=cfg)
    assert "custom portrait" in out


def test_get_document_reports_a_stored_unknown_size_as_the_letter_it_renders(store, cfg_for):
    doc = store.create_document("Old")
    store._db.execute("UPDATE documents SET settings = ? WHERE id = ?",
                      (json.dumps({**doc["settings"], "page_size": "a3"}), doc["id"]))
    store._db.commit()
    out = get_document.invoke({}, config=cfg_for(doc["id"]))
    assert "letter (stored size 'a3' isn't supported)" in out


async def test_mcp_set_document_style_returns_an_error_the_model_can_fix(store):
    server = build_mcp_server()
    did = _text(await server.call_tool("create_document", {"title": "Via MCP"})).split()[1]
    out = _text(await server.call_tool("set_document_style", {"doc_id": did, "page_size": "tabloid"}))
    assert out.startswith("ERROR") and "letter, a4, legal, a5, slide" in out
    assert store.get_document(did)["settings"]["page_size"] == "letter"


def test_config_default_page_size_folds_case_and_degrades_with_a_note():
    cfg = CodraftConfig.resolve(env={"LANGSTAGE_CODRAFT_DEFAULT_PAGE_SIZE": "A4"}, use_toml=False)
    assert cfg.default_page_size == "a4"
    cfg = CodraftConfig.resolve(env={"LANGSTAGE_CODRAFT_DEFAULT_PAGE_SIZE": "A3"}, use_toml=False)
    assert cfg.default_page_size == ""
    issue = next(i for i in cfg.config_issues() if i.get("field") == "default_page_size")
    assert issue["kind"] == "invalid_value" and "a4" in issue["error"]
    cfg = CodraftConfig.resolve(env={"LANGSTAGE_CODRAFT_APPROVAL": "Auto"}, use_toml=False)
    assert cfg.approval == "auto"


def test_new_documents_use_the_configured_size(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LANGSTAGE_CODRAFT_DEFAULT_PAGE_SIZE", "A4")
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []},
                     store=Store(tmp_path / "data" / "codraft.db"))
    with TestClient(app) as c:
        assert c.get("/api/config").json()["default_page_size"] == "a4"
        assert c.post("/api/docs", json={"template": "report"}).json()["settings"]["page_size"] == "a4"


def test_a_template_with_an_unknown_page_size_is_skipped_with_the_reason(tmp_path):
    (tmp_path / "big.json").write_text(json.dumps({"settings": {"page_size": "A3"}}), encoding="utf-8")
    (tmp_path / "ok.json").write_text(json.dumps({"settings": {"page_size": "A4"}}), encoding="utf-8")
    tpl.configure(tmp_path)
    try:
        assert "ok" in tpl.all_templates()
        path, why = tpl.skipped_templates()["big"]
        assert "page_size" in why and "custom" in why
        assert "big.json" in tpl.unknown_template_message("big")
    finally:
        tpl.configure(None)


def test_house_defaults_drop_only_the_settings_no_document_could_use(monkeypatch):
    table = {"doc_defaults": {"margins": {"top": 100, "right": 20, "bottom": 20, "left": 20},
                              "page_w_mm": 297, "page_h_mm": 420, "page_size": "custom",
                              "footer": {"center": "{page}"}}}
    monkeypatch.setattr("langstage_codraft.server.codraft_toml_table", lambda: table)
    house = house_defaults()
    assert house["page_size"] == "custom" and house["page_w_mm"] == 297
    assert house["footer"] == {"center": "{page}"} and "margins" not in house
    (why,) = house_issues()
    assert why.startswith("doc_defaults.margins ignored: settings.margins.top")
