"""0.4.0: Codraft mounted under a sub-path of a host app, branded, embedded, still exporting."""
from __future__ import annotations

import threading
import time

import httpx
import pytest
from conftest import _free_port
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

NAVY = "#002f58"


@pytest.fixture(scope="module")
def hosted(tmp_path_factory):
    import uvicorn

    from langstage_codraft.agent import build_demo_agent
    from langstage_codraft.config import CodraftConfig
    from langstage_codraft.server import create_app, mount

    port = _free_port()
    data = tmp_path_factory.mktemp("hosted")
    cfg = CodraftConfig.resolve(overrides={
        "data_dir": data, "port": port, "host": "127.0.0.1",
        "renderer_url": f"http://127.0.0.1:{port}/write",
    }, use_toml=False)
    codraft = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "Demo", "missing_tools": []}, ui={
        "agent_name": "Harbor", "theme": "light", "embedded": True, "font_family": "Georgia",
        "colors": {"primary": NAVY, "surface": "#faf7f1"}, "starter_prompts": ["Draft an executive summary"],
        "features": {"mcp": False},
    })
    host = FastAPI()
    expired = {"on": False}

    @host.get("/")
    def home():
        return {"host": True}

    @host.get("/host")
    def host_page(doc: str = ""):
        # A host page framing Codraft, recording what Codraft tells it.
        src = f"/write/d/{doc}?embed=1" if doc else "/write/?embed=1"
        return HTMLResponse(
            "<!doctype html><title>Host</title><script>window.got=[];"
            "addEventListener('message', e => window.got.push(e.data));</script>"
            f"<iframe id=f src='{src}' style='width:1300px;height:800px'></iframe>")

    @host.post("/expire")
    def expire(on: bool = True):
        expired["on"] = on
        return {"expired": on}

    @host.middleware("http")
    async def session_gate(request, call_next):
        # Like a host whose sign-in lapsed: its gate answers API calls with 401.
        if expired["on"] and request.url.path.startswith("/write/api/"):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    mount(host, "/write", codraft)
    host.state.expired = expired
    srv = uvicorn.Server(uvicorn.Config(host, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/write/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    yield base
    srv.should_exit = True
    t.join(timeout=10)


def test_embedded_branded_and_exporting(browser, hosted):
    doc = httpx.post(hosted + "/write/api/docs", json={"template": "report", "title": "Harbor plan"}).json()
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{hosted}/write/d/{doc['id']}")
    pg.wait_for_selector(".cd-page")
    # Branding: the host's colours and font on the chrome; no Codraft header brand or theme toggle.
    primary = pg.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--color-primary').trim()")
    assert primary.lower() == NAVY
    assert "Georgia" in pg.evaluate("getComputedStyle(document.body).fontFamily")
    assert pg.locator("[data-testid=brand]").count() == 0
    assert pg.locator("[data-testid=theme-toggle]").count() == 0
    assert pg.evaluate("document.documentElement.classList.contains('cd-embedded')")
    # Routing stays under the mount.
    assert pg.url.endswith(f"/write/d/{doc['id']}")
    pg.locator("[data-testid=tab-chat]").click()
    assert pg.get_attribute("[data-testid=chat-input]", "placeholder") == "Ask Harbor…"
    assert pg.get_by_role("button", name="Draft an executive summary").count() == 1
    # Uploads and the print route resolve under the mount too.
    pg.locator("[data-testid=tab-document]").click()
    assert "MCP" not in pg.locator("main").inner_text()
    ctx.close()
    assert not errors, errors
    # Export renders through the mounted print route.
    r = httpx.get(f"{hosted}/write/api/docs/{doc['id']}/export?format=pdf", timeout=120)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_host_page_handshake(browser, hosted):
    """Dogfood 0.4.1: in a frame, Codraft tells the host where it is (for deep links), doesn't
    grow the host's history, and reports an expired host session instead of a JSON error."""
    doc = httpx.post(hosted + "/write/api/docs", json={"template": "memo", "title": "Frame test"}).json()
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{hosted}/host")
    fr = pg.frame_locator("#f")
    fr.locator("[data-testid=doc-list-item]").first.wait_for()
    before = pg.evaluate("history.length")
    fr.locator("[data-testid=doc-list-item]", has_text="Frame test").click()
    fr.locator(".cd-page").first.wait_for()
    pg.wait_for_function("window.got.some(m => m.type === 'codraft:location' && m.doc)")
    loc = [m for m in pg.evaluate("window.got") if m.get("doc")][-1]
    assert loc["doc"] == doc["id"] and loc["path"] == f"/d/{doc['id']}" and loc["title"] == "Frame test"
    assert pg.evaluate("history.length") == before  # replaceState inside the frame
    # Deep link: the host frames the document directly.
    pg.goto(f"{hosted}/host?doc={doc['id']}")
    pg.frame_locator("#f").locator(".cd-page").first.wait_for()
    # Expired host session: a clear message and a message to the host, not a JSON parse error.
    httpx.post(hosted + "/expire?on=true")
    try:
        fr = pg.frame_locator("#f")
        fr.locator("[data-testid=tab-comments]").click()
        fr.locator("[data-testid=comment-input]").fill("hello")
        fr.locator("[data-testid=comment-add]").click()
        pg.wait_for_function("window.got.some(m => m.type === 'codraft:auth-required')", timeout=10000)
        assert fr.get_by_text("Your session has ended").count() >= 1
        assert fr.get_by_text("Unexpected token").count() == 0
    finally:
        httpx.post(hosted + "/expire?on=false")
    ctx.close()
    assert not errors, errors
