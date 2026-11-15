"""Design history in a real browser (gh #54): the look can be undone, and an undone look
is the old look pixel for pixel.

An agent restyles without approval. The Design tab lists every change to the look, the
agent's turn is one step, "Undo" puts it back, and the notice after a restyling turn offers
the same. A restore changes settings, layouts and reserved areas only, so the exported page
after it is identical to the one before the restyle.
"""
from __future__ import annotations

import io
import socket
import threading
import time
import zipfile

import httpx
import pytest
from conftest import api  # noqa: E402
from PIL import Image, ImageChops

from langstage_codraft.tools import (
    begin_turn,
    reserve_area,
    set_block_layout,
    set_cover,
    set_document_style,
    set_header_footer,
    set_typography,
)

BODY = ("Agents increasingly produce documents rather than chat replies, yet most hosts still treat their "
        "output as a transcript. A document should be co-authored: the person owns the argument and the agent "
        "owns the presentation. ")


def _cfg(doc_id: str) -> dict:
    return {"configurable": {"thread_id": doc_id}}


def _doc(c, title="Field notes"):
    did = c.post("/api/docs", json={"title": title, "template": "report"}).json()["id"]
    ids = []
    for i in range(4):
        ids.append(c.post(f"/api/docs/{did}/blocks", json={"type": "heading", "content": {"text": f"Part {i + 1}", "level": 2}}).json()["id"])
        ids.append(c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": BODY * 3}}).json()["id"])
    return did, ids


def _pages(c, did) -> list[Image.Image]:
    r = c.get(f"/api/docs/{did}/export", params={"format": "png"})
    assert r.status_code == 200, r.text
    if r.headers["content-type"] == "application/zip":
        z = zipfile.ZipFile(io.BytesIO(r.content))
        return [Image.open(io.BytesIO(z.read(n))).convert("RGB") for n in sorted(z.namelist())]
    return [Image.open(io.BytesIO(r.content)).convert("RGB")]


def _same(a: list[Image.Image], b: list[Image.Image]) -> bool:
    return len(a) == len(b) and all(x.size == y.size and ImageChops.difference(x, y).getbbox() is None
                                    for x, y in zip(a, b, strict=True))


def _restyle(did, ids):
    """What a "make it a magazine feature" turn does: one turn, many styling calls."""
    cfg = _cfg(did)
    begin_turn(did, f"restyle-{time.time()}")
    set_document_style.func(columns=2, theme="modern", margin_mm=14, config=cfg)
    set_typography.func(font_body="Lato", font_size_px=12.5, color_accent="#c2410c", heading_case="uppercase",
                        text_align="justify", config=cfg)
    set_cover.func(background="#1d3557", text_color="light", align="bottom", config=cfg)
    set_header_footer.func(header_left="{title}", footer_right="{page} / {pages}", config=cfg)
    set_block_layout.func(block_id=ids[1], full_width=True, variant="lead", dropcap=True, config=cfg)
    set_block_layout.func(block_id=ids[2], variant="rule", config=cfg)
    set_block_layout.func(block_id=ids[5], variant="pullquote", config=cfg)
    reserve_area.func(page=2, x_px=420, y_px=400, w_px=300, h_px=220, config=cfg)


def test_undoing_a_restyle_gives_back_the_same_pages_pixel_for_pixel(server):
    c = api(server)
    did, ids = _doc(c)
    c.patch(f"/api/docs/{did}", json={"settings": {"style": {"font_heading": "'Lora', serif"}, "footer": {"center": "{page}"}}})
    c.patch(f"/api/blocks/{ids[3]}", json={"layout": {"variant": "small"}})
    original = _pages(c, did)
    doc0 = c.get(f"/api/docs/{did}").json()
    _restyle(did, ids)
    restyled = _pages(c, did)
    assert not _same(original, restyled)  # it really is a different look
    steps = c.get(f"/api/docs/{did}/design-history").json()["steps"]
    assert [s["kind"] for s in steps] == ["agent", "author"]
    assert steps[0]["blocks"] == 3 and steps[0]["areas_added"] == 1 and "1 → 2 columns" in steps[0]["summary"]
    # undo the agent's turn
    got = c.post(f"/api/docs/{did}/design-history/{steps[0]['id']}/restore", json={"only": True}).json()
    assert got["result"]["changed"] and got["result"]["blocks"] == 3 and got["result"]["areas_removed"] == 1
    doc1 = got["doc"]
    assert doc1["settings"] == doc0["settings"]
    assert [(b["id"], b["layout"], b["content"], b["n_versions"]) for b in doc1["blocks"]] == \
        [(b["id"], b["layout"], b["content"], b["n_versions"]) for b in doc0["blocks"]]
    assert _same(original, _pages(c, did))
    # and the undo can be undone: the restyle is back, pixel for pixel
    undo = got["result"]["step"]
    c.post(f"/api/docs/{did}/design-history/{undo}/restore", json={"only": True})
    assert _same(restyled, _pages(c, did))
    # "back to before" the author's first change: the template's own look
    first = c.get(f"/api/docs/{did}/design-history").json()["steps"][-1]
    back = c.post(f"/api/docs/{did}/design-history/{first['id']}/restore", json={}).json()["doc"]
    assert back["settings"]["style"] == {} and back["settings"]["columns"] == 1
    assert [b["content"] for b in back["blocks"]] == [b["content"] for b in doc0["blocks"]]


def test_the_design_tab_lists_the_steps_and_undoes_the_agents_restyle(page, server):
    c = api(server)
    did, ids = _doc(c, "History in the editor")
    page.goto(f"{server}/d/{did}")
    page.click("[data-testid=tab-design]")
    page.wait_for_selector("[data-testid=design-history-empty]")
    # the author picks a heading case in the Design tab: a step of their own
    page.select_option("[data-testid=design-panel] select:has(option[value=uppercase])", "uppercase")
    page.wait_for_function("document.querySelectorAll('[data-testid=design-step]').length === 1")
    first = page.locator("[data-testid=design-step]").first
    assert "You" in first.inner_text() and "heading case" in first.inner_text()
    # the agent restyles in one turn
    _restyle(did, ids)
    page.wait_for_function("document.querySelectorAll('[data-testid=design-step]').length === 2")
    top = page.locator("[data-testid=design-step]").first
    assert top.get_attribute("data-kind") == "agent" and "3 block layouts" in top.inner_text()
    assert c.get(f"/api/docs/{did}").json()["settings"]["columns"] == 2
    page.click("[data-testid=undo-agent-design]")
    page.wait_for_function("document.querySelectorAll('[data-testid=design-step]').length === 3")
    doc = c.get(f"/api/docs/{did}").json()
    assert doc["settings"]["columns"] == 1 and doc["settings"]["theme"] == "classic"
    assert doc["settings"]["style"] == {"heading_case": "uppercase"}  # the author's own change stays
    assert all(b["layout"]["variant"] is None for b in doc["blocks"] if b["id"] in (ids[1], ids[2], ids[5]))
    assert not any(b["type"] == "spacer" for b in doc["blocks"])
    assert "Undid a step" in page.locator("[data-testid=design-step-summary]").first.inner_text()
    assert page.locator("[data-testid=undo-agent-design]").count() == 0  # nothing of the agent's left to undo
    # "Back to before this" on the author's first step: the look the document started with
    page.locator("[data-testid=design-step]").last.locator("[data-testid=design-restore]").click()
    page.wait_for_function(f"fetch('/api/docs/{did}').then(r => r.json()).then(d => !d.settings.style.heading_case)")


# ----------------------------------------------- a styling turn, then the notice's Undo
@pytest.fixture(scope="module")
def styling_server(tmp_path_factory):
    """A server whose agent restyles the document on every turn (keyless, scripted)."""
    import uvicorn
    from langchain.agents import create_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from langstage_codraft.config import CodraftConfig
    from langstage_codraft.middleware import CodraftMiddleware
    from langstage_codraft.server import create_app

    class Stylist(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "codraft-stylist"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if isinstance(messages[-1], HumanMessage):
                msg = AIMessage(content="", tool_calls=[
                    {"name": "set_document_style", "args": {"columns": 2}, "id": "c1"},
                    {"name": "set_typography", "args": {"font_size_px": 13, "color_accent": "#c2410c"}, "id": "c2"}])
            else:
                msg = AIMessage(content="Restyled: two columns, smaller type, an orange accent.")
            return ChatResult(generations=[ChatGeneration(message=msg)])

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    data = tmp_path_factory.mktemp("codraft-stylist")
    cfg = CodraftConfig.resolve(overrides={"data_dir": data, "port": port, "host": "127.0.0.1"}, use_toml=False)
    graph = create_agent(Stylist(), middleware=[CodraftMiddleware(attach_images=False)], name="stylist")
    app = create_app(cfg, graph, {"kind": "graph", "label": "Stylist", "missing_tools": []})
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/api/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    yield base
    srv.should_exit = True
    t.join(timeout=10)


def test_after_a_restyling_turn_the_notice_offers_undo(browser, styling_server):
    c = api(styling_server)
    did, _ = _doc(c, "A styling turn")
    ctx = browser.new_context(viewport={"width": 1500, "height": 1000})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    try:
        pg.goto(f"{styling_server}/d/{did}")
        pg.click("[data-testid=tab-chat]")
        pg.fill("[data-testid=chat-input]", "Make this look like a magazine feature.")
        pg.click("[data-testid=chat-send]")
        pg.wait_for_function(
            "[...document.querySelectorAll('[data-testid=toast]')].some((t) => t.textContent.includes('changed the look'))",
            timeout=60000)
        toast = pg.locator("[data-testid=toast]", has_text="changed the look")
        assert "1 → 2 columns" in toast.inner_text()
        assert c.get(f"/api/docs/{did}").json()["settings"]["columns"] == 2
        [step] = c.get(f"/api/docs/{did}/design-history").json()["steps"]
        assert step["kind"] == "agent" and step["settings"]  # the whole turn is one step
        toast.locator("[data-testid=toast-action]").click()
        pg.wait_for_function(f"fetch('/api/docs/{did}').then(r => r.json()).then(d => d.settings.columns === 1)")
        doc = c.get(f"/api/docs/{did}").json()
        assert doc["settings"]["style"] == {}
        steps = c.get(f"/api/docs/{did}/design-history").json()["steps"]
        assert len(steps) == 2 and steps[0]["restore_of"] == step["id"] and steps[0]["kind"] == "author"
    finally:
        ctx.close()
    assert not errors, errors
