"""0.4.0: Codraft mounted under a sub-path of a host app, branded, embedded, still exporting."""
from __future__ import annotations

import threading
import time

import httpx
import pytest
from fastapi import FastAPI

from conftest import _free_port

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

    @host.get("/")
    def home():
        return {"host": True}

    mount(host, "/write", codraft)
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
