"""0.4.0: hosting Codraft inside another app (branding, sub-path mount, access guards)."""
import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from langstage_codraft.config import CodraftConfig
from langstage_codraft.render import get_renderer
from langstage_codraft.server import STATIC_DIR, create_app, mount, resolve_ui
from langstage_codraft.store import Store


def _app(tmp_path, ui=None, **over):
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8798, **over}, use_toml=False)
    return create_app(cfg, None, {"kind": "none"}, store=Store(tmp_path / "data" / "codraft.db"), ui=ui)


def test_ui_overrides_and_filtering(tmp_path):
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path, "agent_name": "Lighthouse"}, use_toml=False)
    ui = resolve_ui(cfg, {"colors": {"primary": "#002f58", "bogus": "x"}, "features": {"mcp": False, "nope": True},
                          "font_family": "Raleway", "embedded": True})
    assert ui["agent_name"] == "Lighthouse" and ui["embedded"] and ui["font_family"] == "Raleway"
    assert ui["colors"] == {"primary": "#002f58"}
    assert ui["features"]["mcp"] is False and "nope" not in ui["features"]


def test_custom_css_file(tmp_path):
    css = tmp_path / "brand.css"
    css.write_text(":root{--color-primary:#002f58}", encoding="utf-8")
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path, "custom_css": str(css)}, use_toml=False)
    assert "#002f58" in resolve_ui(cfg)["custom_css"]


def test_config_exposes_ui_and_user(tmp_path):
    app = _app(tmp_path, ui={"agent_name": "Lighthouse"}, user_header="X-User-Email")
    with TestClient(app) as c:
        cfg = c.get("/api/config", headers={"X-User-Email": "dana.rivera@example.org"}).json()
        assert cfg["ui"]["agent_name"] == "Lighthouse"
        assert cfg["user"] == {"name": "Dana Rivera", "id": "dana.rivera@example.org"}
        assert c.get("/api/config").json()["user"] is None
        # Edits are attributed to the signed-in user, not the client-supplied name.
        doc = c.post("/api/docs", json={"template": "report"}).json()
        bid = doc["blocks"][1]["id"]
        c.patch(f"/api/blocks/{bid}", json={"content": {"text": "Hi"}, "author": "Mallory"},
                headers={"X-User-Email": "dana.rivera@example.org"})
        hist = c.get(f"/api/blocks/{bid}/versions").json()
        assert any(v.get("author") == "Dana Rivera" for v in hist["versions"])


def test_proxy_secret_guard(tmp_path):
    app = _app(tmp_path, proxy_secret="s3cret")
    with TestClient(app, client=("10.1.2.3", 5000)) as remote:
        assert remote.get("/api/config").status_code == 403
        assert remote.get("/api/config", headers={"X-Codraft-Proxy-Secret": "wrong"}).status_code == 403
        assert remote.get("/api/config", headers={"X-Codraft-Proxy-Secret": "s3cret"}).status_code == 200
    with TestClient(_app(tmp_path / "b", proxy_secret="s3cret"), client=("127.0.0.1", 5000)) as local:  # the headless renderer
        assert local.get("/api/config").status_code == 200


def test_basic_auth(tmp_path):
    app = _app(tmp_path, auth_username="ed", auth_password="pw")
    with TestClient(app, client=("10.1.2.3", 5000)) as c:
        r = c.get("/api/config")
        assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
        tok = base64.b64encode(b"ed:pw").decode()
        assert c.get("/api/config", headers={"Authorization": f"Basic {tok}"}).status_code == 200


def test_mount_runs_lifespan_and_injects_base(tmp_path):
    if not (STATIC_DIR / "index.html").is_file():
        import pytest

        pytest.skip("frontend not built")
    host = FastAPI()

    @host.get("/health")
    def health():
        return {"ok": True}

    codraft = _app(tmp_path, proxy_secret="k")
    mount(host, "/codraft", codraft)
    with TestClient(host, client=("10.0.0.9", 1)) as c:
        assert c.get("/health").json() == {"ok": True}
        h = {"X-Codraft-Proxy-Secret": "k"}
        assert c.get("/codraft/api/config", headers=h).status_code == 200
        page = c.get("/codraft/d/doc_x", headers=h).text
        assert '<base href="/codraft/">' in page and '"base": "/codraft"' in page
        # A trusted proxy can tell Codraft its public prefix.
        page = c.get("/codraft/", headers={**h, "X-Forwarded-Prefix": "/api/proxy/codraft"}).text
        assert '<base href="/api/proxy/codraft/">' in page
        assert get_renderer() is not None  # Codraft's startup ran inside the host's lifespan
    assert get_renderer() is None  # and its shutdown too


def test_show_config_masks_secrets(monkeypatch):
    monkeypatch.setenv("LANGSTAGE_CODRAFT_PROXY_SECRET", "topsecret123")
    monkeypatch.setenv("LANGSTAGE_AUTH_PASSWORD", "pw999")
    text = CodraftConfig.resolve(use_toml=False).describe()
    assert "topsecret123" not in text and "pw999" not in text and "********" in text
