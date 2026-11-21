"""An unknown ``--since`` checkpoint isn't reported as a missing document (gh #68).

In-process ``export`` read every 404 as "document not found" and pointed at
``langstage-codraft list``. Since 0.12.0 the route also answers 404 for an unknown
checkpoint, so a typo in ``--since`` told the user their document didn't exist.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from langstage_codraft import cli, standalone
from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.server import create_app
from langstage_codraft.store import Store


@pytest.fixture
def routed(tmp_path, monkeypatch):
    """A Standalone stand-in that answers through the real export route."""
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    st = Store(data / "codraft.db")
    d = st.create_document("Redline")
    st.insert_block(d["id"], "text", {"markdown": "The cat sat on the mat."}, author="author")
    st.create_checkpoint(d["id"], "Sent to legal")
    cfg = CodraftConfig.resolve(overrides={"data_dir": data, "port": 8798}, use_toml=False)
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []}, store=st)

    with TestClient(app) as client:

        class Routed:
            def __init__(self, data_dir=None, *, cfg=None, store=None):
                pass

            def start(self):
                return self

            def close(self):
                pass

            def get(self, doc_id, fmt="pdf", **kw):
                return client.get(f"/api/docs/{doc_id}/export", params={"format": fmt, **kw})

        monkeypatch.setattr(standalone, "Standalone", Routed)
        yield d["id"], data


def test_unknown_checkpoint_names_the_checkpoint(routed, tmp_path):
    doc_id, data = routed
    res = CliRunner().invoke(cli.main, ["export", doc_id, "-f", "docx", "--since", "Sent to legl",
                                        "-o", str(tmp_path / "y.docx"), "--data-dir", str(data)])
    assert res.exit_code == 1
    assert f"{doc_id}: no checkpoint 'Sent to legl' in this document" in res.output
    assert "not found in" not in res.output and "langstage-codraft list" not in res.output


def test_unknown_document_still_points_at_list(routed, tmp_path):
    _, data = routed
    res = CliRunner().invoke(cli.main, ["export", "doc_nope", "-f", "docx", "--since", "Sent to legal",
                                        "-o", str(tmp_path / "y.docx"), "--data-dir", str(data)])
    assert res.exit_code == 1
    assert "doc_nope: not found in" in res.output and "langstage-codraft list" in res.output


def test_route_detail_has_no_stray_quotes(routed):
    doc_id, _ = routed
    r = standalone.Standalone().get(doc_id, "docx", since="Nope")
    assert r.status_code == 404
    assert r.json()["detail"].startswith("no checkpoint 'Nope'")
    assert standalone.Standalone().get("doc_nope", "docx").json()["detail"] == "document doc_nope not found"
