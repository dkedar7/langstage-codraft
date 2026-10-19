"""0.5.2: fixes for the first dogfood routine's filings (gh #2 to #6)."""
import base64
import subprocess
import sys

from click.testing import CliRunner
from fastapi.testclient import TestClient

from langstage_codraft.cli import main
from langstage_codraft.config import CodraftConfig
from langstage_codraft.render import auth_headers
from langstage_codraft.server import create_app
from langstage_codraft.store import Store


def _app(tmp_path, **over):
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799, **over}, use_toml=False)
    return create_app(cfg, None, {"kind": "none"}, store=Store(tmp_path / "data" / "codraft.db"))


# --------------------------------------------------------------- gh #2: basic auth on loopback
def test_basic_auth_applies_to_loopback(tmp_path):
    """Behind a same-host proxy every visitor is 127.0.0.1: the password must still apply."""
    with TestClient(_app(tmp_path, auth_password="pw"), client=("127.0.0.1", 5000)) as c:
        assert c.get("/api/docs").status_code == 401
        assert c.post("/api/docs", json={}).status_code == 401
        tok = base64.b64encode(b"admin:pw").decode()
        assert c.get("/api/docs", headers={"Authorization": f"Basic {tok}"}).status_code == 200


def test_renderer_sends_configured_credentials():
    cfg = CodraftConfig.resolve(overrides={"auth_username": "ed", "auth_password": "pw"}, use_toml=False)
    h = auth_headers(cfg)
    assert h["authorization"] == "Basic " + base64.b64encode(b"ed:pw").decode()
    assert "x-codraft-proxy-secret" not in h
    cfg = CodraftConfig.resolve(overrides={"proxy_secret": "k"}, use_toml=False)
    assert auth_headers(cfg) == {"x-codraft-proxy-secret": "k"}
    assert auth_headers(CodraftConfig.resolve(use_toml=False)) == {}


def test_renderer_credentials_pass_the_guard(tmp_path):
    cfg = CodraftConfig.resolve(overrides={"auth_password": "pw"}, use_toml=False)
    with TestClient(_app(tmp_path, auth_password="pw"), client=("127.0.0.1", 5000)) as c:
        assert c.get("/api/docs", headers=auth_headers(cfg)).status_code == 200


# --------------------------------------------------------------- gh #3: cross-site requests
def test_cross_site_form_post_is_refused(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        doc = c.post("/api/docs", json={"template": "report"}).json()
        evil = {"Origin": "https://evil.example", "content-type": "application/x-www-form-urlencoded"}
        r = c.post(f"/api/docs/{doc['id']}/suggestions/accept-all", headers=evil, content=b"")
        assert r.status_code == 403 and "Cross-site request refused" in r.json()["detail"]
        assert c.post("/api/docs", headers={"Origin": "null", "content-type": "text/plain"}).status_code == 403
        r = c.post(f"/api/docs/{doc['id']}/uploads", headers={"Sec-Fetch-Site": "cross-site"},
                   files={"file": ("x.txt", b"hi", "text/plain")})
        assert r.status_code == 403
        # Safe methods are never refused, even cross-site.
        assert c.get("/api/docs", headers={"Origin": "https://evil.example"}).status_code == 200


def test_own_and_loopback_origins_pass(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.post("/api/docs", json={}, headers={"Origin": "http://testserver"}).status_code == 200
        assert c.post("/api/docs", json={}, headers={"Origin": "http://localhost:5173"}).status_code == 200
        assert c.post("/api/docs", json={}, headers={"Origin": "http://[::1]:8060"}).status_code == 200
        # A proxy that rewrites Host: the browser's same-origin attestation is enough.
        h = {"Origin": "https://docs.example.org", "Sec-Fetch-Site": "same-origin"}
        assert c.post("/api/docs", json={}, headers=h).status_code == 200
        # curl, the CLI and MCP clients send neither header.
        assert c.post("/api/docs", json={}).status_code == 200


# --------------------------------------------------------------- gh #4: `new` matches New document
def test_cli_new_honours_house_defaults_page_size_and_title(tmp_path, monkeypatch):
    (tmp_path / "langstage.toml").write_text(
        '[codraft]\ndefault_page_size = "a4"\ndata_dir = "data"\n\n'
        '[codraft.doc_defaults]\ncover = { enabled = false }\nfooter = { left = "{page}", right = "Acme Corp" }\n',
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["new", "Quarterly review", "--template", "report"])
    assert r.exit_code == 0, r.output
    d = Store(tmp_path / "data" / "codraft.db").full(r.output.strip())
    s = d["settings"]
    assert s["page_size"] == "a4" and s["cover"]["enabled"] is False and s["footer"]["right"] == "Acme Corp"
    assert d["blocks"][0]["content"]["text"] == "Quarterly review"
    assert d["title"] == "Quarterly review"


def test_cli_new_and_api_create_the_same_document(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LANGSTAGE_CODRAFT_DEFAULT_PAGE_SIZE", "a4")
    r = CliRunner().invoke(main, ["new", "Plan", "--template", "memo", "--data-dir", str(tmp_path / "data")])
    assert r.exit_code == 0, r.output
    cli_doc = Store(tmp_path / "data" / "codraft.db").full(r.output.strip())
    with TestClient(_app(tmp_path / "api", default_page_size="a4")) as c:
        api_doc = c.post("/api/docs", json={"title": "Plan", "template": "memo"}).json()
    assert cli_doc["settings"] == api_doc["settings"]
    assert [b["content"] for b in cli_doc["blocks"]] == [b["content"] for b in api_doc["blocks"]]


# --------------------------------------------------------------- gh #5: cp1252 consoles
def test_list_survives_a_cp1252_stdout(tmp_path):
    data = str(tmp_path / "d")
    env = {**__import__("os").environ, "PYTHONIOENCODING": "cp1252"}
    run = [sys.executable, "-m", "langstage_codraft.cli"]
    subprocess.run([*run, "new", "日本語 ✓ report", "--data-dir", data], check=True, env=env,
                   capture_output=True, cwd=tmp_path)
    r = subprocess.run([*run, "list", "--data-dir", data], env=env, capture_output=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert b"report" in r.stdout and b"Traceback" not in r.stderr


# --------------------------------------------------------------- gh #6: custom_css is toml-relative
def test_relative_custom_css_resolves_against_the_toml(tmp_path, monkeypatch):
    (tmp_path / "langstage.toml").write_text('[ui]\ncustom_css = "brand.css"\n', encoding="utf-8")
    (tmp_path / "brand.css").write_text(".brand-marker{color:red}", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path / "sub")
    cfg = CodraftConfig.resolve()
    assert cfg.custom_css == str(tmp_path / "brand.css")
    from langstage_codraft.server import resolve_ui

    assert ".brand-marker" in resolve_ui(cfg)["custom_css"]


def test_inline_custom_css_in_toml_is_left_alone(tmp_path, monkeypatch):
    (tmp_path / "langstage.toml").write_text('[ui]\ncustom_css = ":root{--color-primary:#002f58}"\n',
                                             encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert CodraftConfig.resolve().custom_css == ":root{--color-primary:#002f58}"


# --------------------------------------------------------------- gh #8: secret + password together
def test_proxy_secret_and_basic_auth_are_both_enforced(tmp_path):
    tok = {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}
    sec = {"X-Codraft-Proxy-Secret": "k"}
    app = _app(tmp_path, proxy_secret="k", auth_password="pw")
    with TestClient(app, client=("127.0.0.1", 5000)) as c:  # a same-host proxy: no secret needed, password is
        assert c.get("/api/docs").status_code == 401
        assert c.post("/api/docs", json={}).status_code == 401
        assert c.get("/api/docs", headers=sec).status_code == 401
        assert c.get("/api/docs", headers=tok).status_code == 200
    with TestClient(_app(tmp_path / "b", proxy_secret="k", auth_password="pw"), client=("10.1.2.3", 5000)) as c:
        assert c.get("/api/docs", headers=sec).status_code == 401
        assert c.get("/api/docs", headers=tok).status_code == 403
        assert c.get("/api/docs", headers={**sec, **tok}).status_code == 200
    cfg = CodraftConfig.resolve(overrides={"proxy_secret": "k", "auth_password": "pw"}, use_toml=False)
    with TestClient(_app(tmp_path / "c", proxy_secret="k", auth_password="pw"), client=("10.1.2.3", 5000)) as c:
        assert c.get("/api/docs", headers=auth_headers(cfg)).status_code == 200  # what the renderer sends


# --------------------------------------------------------------- gh #9: mcp = false
def test_mcp_feature_off_unmounts_the_endpoint(tmp_path):
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    off = create_app(cfg, None, {"kind": "none"}, store=Store(tmp_path / "data" / "codraft.db"),
                     ui={"features": {"mcp": False}})
    with TestClient(off) as c:
        assert c.get("/api/config").json()["mcp_url"] is None
        assert c.post("/mcp", json={}).status_code in (404, 405)  # no MCP route (the SPA is GET-only)
    on = create_app(cfg, None, {"kind": "none"}, store=Store(tmp_path / "data" / "codraft.db"))
    with TestClient(on) as c:
        assert c.get("/api/config").json()["mcp_url"] == "/mcp"
        assert c.post("/mcp", json={}).status_code != 404


# --------------------------------------------------------------- langstage#208: DNS rebinding
def _guarded(tmp_path, **over):
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799, "host": "localhost", **over},
                                use_toml=False)
    return create_app(cfg, None, {"kind": "none"}, store=Store(tmp_path / "data" / "codraft.db"), host_guard=True)


def test_rebound_host_is_refused_on_every_method(tmp_path):
    with TestClient(_guarded(tmp_path), base_url="http://evil.example:8799") as c:
        r = c.get("/api/docs")
        assert r.status_code == 403 and "allowed_hosts" in r.json()["detail"] and "evil.example" in r.json()["detail"]
        assert c.post("/api/docs", json={}).status_code == 403
        assert c.post("/mcp", json={}).status_code == 403


def test_loopback_names_pass(tmp_path):
    with TestClient(_guarded(tmp_path)) as c:
        for host in ("localhost:8799", "127.0.0.1:8799", "[::1]:8799", "app.localhost:8799", "LOCALHOST"):
            assert c.get("/api/docs", headers={"host": host}).status_code == 200, host


def test_allowed_hosts_and_star(tmp_path):
    with TestClient(_guarded(tmp_path, allowed_hosts=["docs.example.org", "*.example.net"])) as c:
        assert c.get("/api/docs", headers={"host": "DOCS.example.org"}).status_code == 200
        assert c.get("/api/docs", headers={"host": "a.b.example.net:443"}).status_code == 200
        assert c.get("/api/docs", headers={"host": "example.net"}).status_code == 403
    with TestClient(_guarded(tmp_path / "s", allowed_hosts=["*"]), base_url="http://evil.example") as c:
        assert c.get("/api/docs").status_code == 200


def test_guard_off_for_public_binds_and_mounted_apps(tmp_path):
    with TestClient(_guarded(tmp_path, host="0.0.0.0"), base_url="http://evil.example") as c:
        assert c.get("/api/docs").status_code == 200
    with TestClient(_app(tmp_path / "m"), base_url="http://docs.example.org") as c:  # create_app default: off
        assert c.get("/api/docs").status_code == 200


def test_allowed_hosts_from_env(monkeypatch):
    monkeypatch.setenv("LANGSTAGE_ALLOWED_HOSTS", "docs.example.org, *.example.net")
    assert CodraftConfig.resolve(use_toml=False).allowed_hosts == ["docs.example.org", "*.example.net"]
