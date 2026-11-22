"""``export`` with one document and ``-o DIR`` writes into the directory (gh #70).

Only the several-exports branch treated ``-o`` as a directory. A single export to an
existing directory (``-o out/``, ``-o .``) crashed with IsADirectoryError, and a new
directory written with a trailing slash (``-o newdir/``) became a file named ``newdir``,
because pathlib drops the slash.
"""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from langstage_codraft import cli, standalone

NAME = "Plan.docx"  # what the route's Content-Disposition suggests


def _response() -> httpx.Response:
    return httpx.Response(200, content=b"PK-docx-bytes",
                          headers={"content-disposition": f'attachment; filename="{NAME}"',
                                   "content-type": "application/octet-stream"})


@pytest.fixture
def routed(tmp_path, monkeypatch):
    """Standalone and httpx stand-ins that answer like the export route (no Chromium)."""
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    data.mkdir()

    class Routed:
        def __init__(self, data_dir=None, *, cfg=None, store=None):
            pass

        def start(self):
            return self

        def close(self):
            pass

        def get(self, doc_id, fmt="pdf", **kw):
            return _response()

    monkeypatch.setattr(standalone, "Standalone", Routed)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _response())  # the -p PORT path
    yield "doc_plan", data, NAME


def _export(doc_id, data, *out):
    return CliRunner().invoke(cli.main, ["export", doc_id, "-f", "docx", *out, "--data-dir", str(data)])


def test_existing_directory(routed, tmp_path):
    doc_id, data, name = routed
    (tmp_path / "out").mkdir()
    res = _export(doc_id, data, "-o", "out")
    assert res.exit_code == 0, res.output
    assert (tmp_path / "out" / name).read_bytes() == b"PK-docx-bytes"
    assert f"wrote {tmp_path.joinpath('out', name).relative_to(tmp_path)}" in res.output


def test_dot_is_the_current_directory(routed, tmp_path):
    doc_id, data, name = routed
    res = _export(doc_id, data, "-o", ".")
    assert res.exit_code == 0, res.output
    assert (tmp_path / name).is_file()


@pytest.mark.parametrize("sep", cli._SEPARATORS)
def test_new_directory_with_trailing_separator(routed, tmp_path, sep):
    doc_id, data, name = routed
    res = _export(doc_id, data, "-o", f"newdir{sep}")
    assert res.exit_code == 0, res.output
    assert (tmp_path / "newdir").is_dir()
    assert (tmp_path / "newdir" / name).is_file()


def test_plain_file_path_still_a_file(routed, tmp_path):
    doc_id, data, _ = routed
    res = _export(doc_id, data, "-o", "plan-final.docx")
    assert res.exit_code == 0, res.output
    assert (tmp_path / "plan-final.docx").is_file()


def test_trailing_slash_on_an_existing_file_is_a_usage_error(routed, tmp_path):
    doc_id, data, _ = routed
    (tmp_path / "taken").write_text("x")
    res = _export(doc_id, data, "-o", "taken/")
    assert res.exit_code == 2
    assert "is a file" in res.output
    assert (tmp_path / "taken").read_text() == "x"


def test_through_a_running_server(routed, tmp_path):
    doc_id, _, name = routed
    (tmp_path / "out").mkdir()
    res = CliRunner().invoke(cli.main, ["export", doc_id, "-f", "docx", "-o", "out/", "-p", "8799"])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "out" / name).is_file()
