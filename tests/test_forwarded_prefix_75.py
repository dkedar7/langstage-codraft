"""gh #75: a forwarded prefix that arrives with the proxy secret is still only a URL path.

A hostile ``X-Forwarded-Prefix`` must neither break out of the inline script nor move
``<base href>`` (and so the app bundle) to another host."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from langstage_codraft.config import CodraftConfig
from langstage_codraft.server import STATIC_DIR, _index_html, _safe_prefix, create_app
from langstage_codraft.store import Store

BS = chr(92)
HOSTILE = [
    "//evil.example",
    "/" + BS + "evil.example",
    BS + BS + "evil.example",
    '/x"</script><script>window.__pwned=1</script>',
    "javascript:alert(1)",
    "/a/../../evil",
    "/..",
    "/./x",
    "/a b",
    "evil.example",
    "/ok\n<script>",
]


def _app(tmp_path, **over):
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8798, **over}, use_toml=False)
    return create_app(cfg, None, {"kind": "none"}, store=Store(tmp_path / "data" / "codraft.db"))


def _page_base(page: str) -> str:
    return re.search(r'<base href="([^"]*)">', page).group(1)


@pytest.mark.parametrize("value", HOSTILE)
def test_safe_prefix_rejects_anything_but_a_plain_path(value):
    assert _safe_prefix(value) is None


@pytest.mark.parametrize("value,expected", [("", ""), ("/", ""), ("/write", "/write"), ("/write/", "/write"),
                                            ("/api/proxy/codraft", "/api/proxy/codraft"), ("/a%20b/c-d_e.f~", "/a%20b/c-d_e.f~")])
def test_safe_prefix_keeps_ordinary_paths(value, expected):
    assert _safe_prefix(value) == expected


def test_inline_script_cannot_be_closed_whatever_base_is(tmp_path):
    index = tmp_path / "index.html"
    index.write_text("<html><head><title>x</title></head><body></body></html>", encoding="utf-8")
    base = '/x"</script><script>alert(1)</script>&' + chr(0x2028)
    page = _index_html(index, base)
    head = page.split("<title>")[0]
    assert head.count("</script>") == 1  # only the injected script's own closing tag
    script = re.search(r"window\.__CODRAFT__=(.*?);</script>", head).group(1)
    assert "<" not in script and ">" not in script and "&" not in script
    assert json.loads(script) == {"base": base}  # still the same value once parsed


@pytest.fixture
def built():
    if not (STATIC_DIR / "index.html").is_file():
        pytest.skip("frontend not built")


@pytest.mark.parametrize("value", HOSTILE)
def test_hostile_prefix_with_the_secret_is_ignored(tmp_path, built, value):
    with TestClient(_app(tmp_path, proxy_secret="k"), client=("203.0.113.5", 9)) as c:
        try:
            r = c.get("/", headers={"X-Codraft-Proxy-Secret": "k", "X-Forwarded-Prefix": value})
        except Exception:  # a header value httpx itself refuses (a newline) can't reach the app
            return
        page = r.text
        assert _page_base(page) == "/"
        assert "__pwned" not in page and "evil.example" not in page
        assert json.loads(re.search(r"window\.__CODRAFT__=(.*?);</script>", page).group(1)) == {"base": ""}


def test_hostile_prefix_falls_back_to_base_path(tmp_path, built):
    with TestClient(_app(tmp_path, proxy_secret="k", base_path="/write"), client=("203.0.113.5", 9)) as c:
        page = c.get("/", headers={"X-Codraft-Proxy-Secret": "k", "X-Forwarded-Prefix": "//evil.example"}).text
        assert _page_base(page) == "/write/"


def test_good_prefix_with_the_secret_still_wins(tmp_path, built):
    with TestClient(_app(tmp_path, proxy_secret="k", base_path="/write"), client=("203.0.113.5", 9)) as c:
        page = c.get("/d/abc", headers={"X-Codraft-Proxy-Secret": "k", "X-Forwarded-Prefix": "/api/proxy/codraft/"}).text
        assert _page_base(page) == "/api/proxy/codraft/"


@pytest.mark.parametrize("bp,expected", [("write", "/write/"), ("/write/", "/write/"), ("//evil.example", "/"),
                                         ("https://evil.example", "/")])
def test_base_path_config_cannot_point_at_another_host(tmp_path, built, bp, expected):
    with TestClient(_app(tmp_path, base_path=bp), client=("203.0.113.5", 9)) as c:
        assert _page_base(c.get("/").text) == expected
