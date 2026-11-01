"""Shared fixtures for the browser end-to-end tests: a live keyless demo server and Chromium."""
from __future__ import annotations

import io
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest

STATIC = Path(__file__).resolve().parents[2] / "langstage_codraft" / "static" / "index.html"


def pytest_collection_modifyitems(items):
    if not STATIC.is_file():
        skip = pytest.mark.skip(reason="frontend not built")
        for it in items:
            it.add_marker(skip)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn

    from langstage_codraft.agent import build_demo_agent
    from langstage_codraft.config import CodraftConfig
    from langstage_codraft.server import create_app

    port = _free_port()
    data = tmp_path_factory.mktemp("codraft")
    cfg = CodraftConfig.resolve(overrides={"data_dir": data, "port": port, "host": "127.0.0.1"}, use_toml=False)
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "Demo co-author", "missing_tools": []})
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    yield base
    srv.should_exit = True
    t.join(timeout=10)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    ctx = browser.new_context(viewport={"width": 1500, "height": 1000}, accept_downloads=True)
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.errors = errors  # type: ignore[attr-defined]
    yield pg
    ctx.close()
    assert not errors, errors


def api(server):  # noqa: D103
    return httpx.Client(base_url=server, timeout=120)




def page_image(pg, selector: str, scale: float = 2):
    """The page under ``selector`` as Codraft's export captures it: exactly the rows
    Chromium paints for it, padded to the full page size (gh #32). A plain element
    screenshot rounds a fractional clip outward and picks up a row of what's above."""
    import math

    from PIL import Image

    from langstage_codraft.render import _pad_to

    loc = pg.locator(selector)
    loc.scroll_into_view_if_needed()
    x, y, w, h = loc.evaluate(
        "e => { const r = e.getBoundingClientRect(); return [r.left + scrollX, r.top + scrollY, r.width, r.height]; }")
    px = lambda v: math.floor(v + 0.5)  # noqa: E731
    x0, y0 = px(x), px(y)
    png = pg.screenshot(full_page=True, clip={"x": x0, "y": y0, "width": px(x + w) - x0, "height": px(y + h) - y0})
    im = Image.open(io.BytesIO(png)).convert("RGB")
    return _pad_to(im, (round(math.ceil(w - 1e-6) * scale), round(math.ceil(h - 1e-6) * scale)))
