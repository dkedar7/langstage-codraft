"""Export file names keep the title in any script (gh #79).

The name came from ``re.sub(r"[^A-Za-z0-9]+", "-", title)``, so accented letters were cut
out (``rapport-d-activit-2026.pdf``) and a title in any non-Latin script downloaded as
``document.pdf``. Now accented Latin letters are spelled without their marks, every other
script keeps its letters, and the route sends the real name as RFC 5987 ``filename*`` next
to an ASCII ``filename``. The CLI and the editor read ``filename*`` first.
"""
from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from test_redline_65 import DocxRenderer

from langstage_codraft import cli, filenames, standalone
from langstage_codraft import server as server_mod
from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.server import create_app
from langstage_codraft.store import Store

TITLES = [
    ("Rapport d'activité 2026", "rapport-d-activite-2026", "rapport-d-activite-2026"),
    ("Größenbericht Übersicht", "grossenbericht-ubersicht", "grossenbericht-ubersicht"),
    ("日本の報告", "日本の報告", "document"),
    ("Отчёт за год", "отчёт-за-год", "document"),
]


@pytest.mark.parametrize(("title", "real", "plain"), TITLES)
def test_the_stem_keeps_the_title(title, real, plain):
    assert filenames.export_stem(title) == real
    assert filenames.ascii_stem(title) == plain


@pytest.mark.parametrize(("title", "real", "plain"), TITLES)
def test_the_header_carries_both_names(title, real, plain):
    disp = filenames.content_disposition(title, ".pdf")
    assert disp.startswith(f'attachment; filename="{plain}.pdf"')
    if real == plain:
        assert "filename*" not in disp
    else:
        assert f"filename*=UTF-8''{quote(real + '.pdf', safe='')}" in disp
    assert disp.isascii()  # a header value must be
    assert filenames.filename_from_disposition(disp) == f"{real}.pdf"


def test_scripts_with_combining_marks_stay_whole():
    # Japanese voiced kana and Devanagari vowel signs are a base plus a mark: neither is Latin.
    assert filenames.export_stem("がぎぐ報告") == "がぎぐ報告"
    assert filenames.export_stem("हिन्दी रिपोर्ट") == "हिन्दी-रिपोर्ट"
    assert filenames.export_stem("  Q3 / plan: \"draft\"  ") == "q3-plan-draft"
    assert filenames.export_stem("") == "document"
    assert len(filenames.export_stem("x" * 200)) == filenames.MAX_STEM


@pytest.mark.parametrize("disp", [
    'attachment; filename="../../etc/passwd"',
    "attachment; filename*=UTF-8''..%2F..%2Fsecret.pdf",
    'attachment; filename="a:b*c?.pdf"',
])
def test_a_suggested_name_is_never_a_path(disp):
    name = filenames.filename_from_disposition(disp)
    assert name and "/" not in name and "\\" not in name and ":" not in name
    assert name not in (".", "..")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    st = Store(tmp_path / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []}, store=st)
    with TestClient(app) as c:
        fake = DocxRenderer(server_mod.get_renderer())
        monkeypatch.setattr(server_mod, "get_renderer", lambda: fake)
        yield c


@pytest.mark.parametrize(("title", "real", "plain"), TITLES)
def test_the_export_route_names_the_file_after_the_title(client, title, real, plain):
    did = client.post("/api/docs", json={"title": title}).json()["id"]
    r = client.get(f"/api/docs/{did}/export", params={"format": "docx"})
    assert r.status_code == 200, r.text
    disp = r.headers["content-disposition"]
    assert f'filename="{plain}.docx"' in disp
    assert filenames.filename_from_disposition(disp) == f"{real}.docx"


@pytest.fixture
def routed(tmp_path, monkeypatch):
    """A stand-in Standalone that answers each document like the export route (no Chromium)."""
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    titles = {"doc_a": "Rapport d'activité 2026", "doc_b": "日本の報告", "doc_c": "日本の報告", "doc_d": "Отчёт за год"}

    class Routed:
        def __init__(self, data_dir=None, *, cfg=None, store=None):
            pass

        def start(self):
            return self

        def close(self):
            pass

        def get(self, doc_id, fmt="pdf", **kw):
            return httpx.Response(200, content=doc_id.encode(), headers={
                "content-disposition": filenames.content_disposition(titles[doc_id], f".{fmt}"),
                "content-type": "application/pdf"})

    monkeypatch.setattr(standalone, "Standalone", Routed)
    return data


def test_the_cli_writes_each_title_into_the_directory(routed, tmp_path):
    res = CliRunner().invoke(cli.main, ["export", "doc_a", "doc_b", "doc_c", "doc_d", "-f", "pdf",
                                        "-o", "out/", "--data-dir", str(routed)])
    assert res.exit_code == 0, res.output
    got = {p.name: p.read_bytes() for p in (tmp_path / "out").iterdir()}
    assert got == {
        "rapport-d-activite-2026.pdf": b"doc_a",
        "日本の報告.pdf": b"doc_b",
        "doc_c-日本の報告.pdf": b"doc_c",  # the same title twice: the collision guard still applies
        "отчёт-за-год.pdf": b"doc_d",
    }
