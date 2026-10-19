import httpx
import pytest
from click.testing import CliRunner

from langstage_codraft import cli


@pytest.mark.parametrize("fmt", cli.EXPORT_FORMATS)
def test_export_accepts_every_server_format(fmt, monkeypatch, tmp_path):
    # The README lists docx/pptx/gif/mp4 for `export`; the CLI used to reject them.
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["format"] = params["format"]
        return httpx.Response(200, content=b"x", headers={"content-type": "application/octet-stream"})

    monkeypatch.setattr(httpx, "get", fake_get)
    out = tmp_path / f"out.{fmt}"
    res = CliRunner().invoke(cli.main, ["export", "doc_1", "-f", fmt, "-o", str(out), "-p", "1"])
    assert res.exit_code == 0, res.output
    assert seen["format"] == fmt
    assert out.read_bytes() == b"x"

