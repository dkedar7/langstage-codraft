"""A refused write leaves nothing behind (gh #28); a broken template file says why (gh #27)."""
import logging

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from langstage_codraft import cli
from langstage_codraft import templates as tpl
from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.server import create_app
from langstage_codraft.store import Invalid, NotFound, Store


def _summary(store, doc_id):
    return [(b["content"].get("markdown"), b["n_versions"]) for b in store.list_blocks(doc_id)]


# ------------------------------------------------------------------------ gh #28
@pytest.mark.parametrize("content", ["Hello", ["x"], 3])
def test_insert_with_non_object_content_is_refused_and_writes_nothing(store, content):
    d = store.create_document("D")
    with pytest.raises(Invalid, match="content must be an object"):
        store.insert_block(d["id"], "text", content)
    store.insert_block(d["id"], "text", {"markdown": "Real"})  # the next commit used to keep the ghost
    assert _summary(store, d["id"]) == [("Real", 1)]


def test_edit_with_non_object_content_is_refused(store):
    d = store.create_document("D")
    b = store.insert_block(d["id"], "text", {"markdown": "A"})
    with pytest.raises(Invalid, match="content must be an object"):
        store.edit_block(b["id"], content="B")
    assert store.get_block(b["id"])["n_versions"] == 1


def test_a_refused_move_does_not_half_move_the_block(store):
    d = store.create_document("D")
    store.insert_block(d["id"], "text", {"markdown": "A"})
    b = store.insert_block(d["id"], "text", {"markdown": "B"})
    with pytest.raises(NotFound):
        store.move_block(b["id"], "blk_missing")
    store.insert_block(d["id"], "text", {"markdown": "C"})
    assert [m for m, _ in _summary(store, d["id"])] == ["A", "B", "C"]


def test_a_failing_write_rolls_back_everything_it_wrote(store):
    """The store's lock is the transaction: an error after a write undoes the write."""
    d = store.create_document("D")
    with pytest.raises(RuntimeError):
        with store._lock:
            store._db.execute("UPDATE documents SET title = 'changed' WHERE id = ?", (d["id"],))
            raise RuntimeError("boom")
    store.insert_block(d["id"], "text", {"markdown": "x"})  # commits
    assert store.get_document(d["id"])["title"] == "D"


def test_a_template_block_with_bad_content_creates_no_document(store):
    before = len(store.list_documents())
    with pytest.raises(Invalid):
        store.create_document("T", {}, [{"type": "text", "content": {"markdown": "ok"}},
                                         {"type": "text", "content": "bad"}])
    store.create_document("Other")
    assert len(store.list_documents()) == before + 1


def test_http_insert_with_string_content_is_a_4xx_and_leaves_no_ghost(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    store = Store(tmp_path / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []},
                     store=store)
    d = store.create_document("Ghost")
    with TestClient(app) as c:
        r = c.post(f"/api/docs/{d['id']}/blocks", json={"type": "text", "content": "Hello"})
        assert r.status_code == 400 and "content must be an object" in r.text
        assert c.post(f"/api/docs/{d['id']}/blocks",
                      json={"type": "text", "content": {"markdown": "Real"}}).status_code == 200
        blocks = c.get(f"/api/docs/{d['id']}").json()["blocks"]
    assert [(b["content"], b["n_versions"]) for b in blocks] == [({"markdown": "Real"}, 1)]


# ------------------------------------------------------------------------ gh #27
BROKEN = '{"name": "Team brief", "blocks": [{"type": "text", "content": {"markdown": "Context"}},]}'


@pytest.fixture
def tdir(tmp_path):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "brief.json").write_text(BROKEN, encoding="utf-8")
    (d / "list.json").write_text("[1, 2]", encoding="utf-8")
    (d / "badblock.json").write_text('{"blocks": [{"type": "text", "content": "x"}]}', encoding="utf-8")
    (d / "good.json").write_text('{"name": "Good", "blocks": []}', encoding="utf-8")
    tpl.configure(d)
    yield d
    tpl.configure(None)


def test_skipped_templates_name_the_file_and_the_error(tdir):
    assert "good" in tpl.all_templates() and "brief" not in tpl.all_templates()
    skipped = tpl.skipped_templates()
    assert set(skipped) == {"brief", "list", "badblock"}
    assert "invalid JSON" in skipped["brief"][1] and "line 1" in skipped["brief"][1]
    assert "expected a JSON object" in skipped["list"][1]
    assert "blocks[0].content" in skipped["badblock"][1]
    msg = tpl.unknown_template_message("brief")
    assert "brief.json" in msg and "invalid JSON" in msg
    assert tpl.unknown_template_message("nope").startswith("unknown template 'nope'; available:")


def _toml(tmp_path, monkeypatch):
    (tmp_path / "langstage.toml").write_text(
        '[codraft]\ntemplates_dir = "templates"\ndata_dir = ".codraft"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)


def test_cli_new_names_the_parse_error(tdir, tmp_path, monkeypatch):
    _toml(tmp_path, monkeypatch)
    res = CliRunner().invoke(cli.main, ["new", "Q3 brief", "--template", "brief"])
    assert res.exit_code == 2
    assert "brief.json" in res.output and "invalid JSON" in res.output
    assert "unknown template" not in res.output
    ok = CliRunner().invoke(cli.main, ["new", "Fine", "--template", "good"])
    assert ok.exit_code == 0, ok.output


def test_check_warns_about_each_skipped_template(tdir, tmp_path, monkeypatch):
    _toml(tmp_path, monkeypatch)
    res = CliRunner().invoke(cli.main, ["check", "--demo", "--json"])
    import json

    checks = [c for c in json.loads(res.output)["checks"] if c["name"] == "templates"]
    assert len(checks) == 3 and all(c["ok"] is None for c in checks)
    assert any("brief.json" in c["detail"] and "invalid JSON" in c["detail"] for c in checks)


def test_server_logs_skipped_templates(tdir, tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(
        overrides={"data_dir": tmp_path / "data", "port": 8799, "templates_dir": tdir}, use_toml=False)
    with caplog.at_level(logging.WARNING, logger="langstage_codraft"):
        create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []},
                   store=Store(tmp_path / "data" / "codraft.db"))
    assert any("brief.json" in r.getMessage() and "skipped" in r.getMessage() for r in caplog.records)
