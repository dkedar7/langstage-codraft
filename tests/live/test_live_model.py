"""Opt-in real-model test (costs a few cents). Run with:

    OPENROUTER_API_KEY=... CODRAFT_LIVE=1 pytest tests/live -s

Checks success criterion 3: "address all comments" handles comments top to bottom,
proposes suggestions (with references where asked), and the agent uses view_pages
when asked about layout.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not (os.environ.get("CODRAFT_LIVE") and (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"))),
    reason="set CODRAFT_LIVE=1 and a model API key",
)

MODEL = os.environ.get("CODRAFT_LIVE_MODEL") or None


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn

    from langstage_codraft.agent import resolve
    from langstage_codraft.config import CodraftConfig
    from langstage_codraft.server import create_app

    port = _port()
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path_factory.mktemp("live"), "port": port,
                                           "host": "127.0.0.1", "model": MODEL}, use_toml=False)
    graph, info = resolve(cfg)
    print("model:", info["label"])
    app = create_app(cfg, graph, info)
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


def chat(c: httpx.Client, did: str, message: str) -> list[dict]:
    frames = []
    with c.stream("POST", f"/api/docs/{did}/chat", json={"message": message}, timeout=300) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                frames.append(json.loads(line[6:]))
    return frames


def test_address_comments_top_to_bottom(server):
    c = httpx.Client(base_url=server, timeout=120)
    doc = c.post("/api/docs", json={"title": "Heat pumps memo", "settings": {
        "brief": {"audience": "city council", "objective": "convince", "writing_style": "professional / formal"}}}).json()
    did = doc["id"]
    a = c.post(f"/api/docs/{did}/blocks", json={"type": "heading", "content": {"text": "why heat pumps", "level": 1}}).json()
    b = c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown":
        "heat pumps are good. they use electricity and move heat instead of making it. lots of cities use them now and its cheaper over time probably."}}).json()
    d = c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown":
        "We should start a pilot program next year with 50 homes."}}).json()
    # Created out of order on purpose: the agent must still go top to bottom.
    c.post(f"/api/docs/{did}/comments", json={"body": "Add one concrete number on cost or efficiency, and cite a source for it.", "block_id": d["id"]})
    c.post(f"/api/docs/{did}/comments", json={"body": "Rewrite this formally for the council; fix capitalisation.", "block_id": b["id"]})
    c.post(f"/api/docs/{did}/comments", json={"body": "Title case, please.", "block_id": a["id"]})

    frames = chat(c, did, "Address all comments.")
    assert frames[-1]["type"] == "complete", frames[-5:]
    edits = [f for f in frames if f["type"] == "tool_start" and f["name"] in ("propose_edit", "propose_insert")]
    touched = [f["args"].get("block_id") for f in edits if f["args"].get("block_id")]
    print("edit order:", touched)
    order = [x for x in touched if x in (a["id"], b["id"], d["id"])]
    first_seen = list(dict.fromkeys(order))
    assert first_seen == [a["id"], b["id"], d["id"]], first_seen
    full = c.get(f"/api/docs/{did}").json()
    assert len([s for s in full["suggestions"] if s["status"] == "pending"]) >= 3
    # the text is untouched until the author approves
    assert full["blocks"][1]["content"]["markdown"].startswith("heat pumps are good")
    cited = [s for s in full["suggestions"] if s["block_id"] == d["id"] and s["payload"].get("refs")]
    print("refs on pilot block:", [s["payload"]["refs"] for s in cited])
    for s in full["suggestions"]:
        print(s["kind"], s["block_id"], "->", json.dumps(s["payload"].get("content"))[:160])


def test_view_pages_for_layout(server):
    c = httpx.Client(base_url=server, timeout=120)
    doc = c.post("/api/docs", json={"template": "one-pager", "title": "Layout check"}).json()
    did = doc["id"]
    frames = chat(c, did, "Look at the rendered pages and tell me in two sentences what you see about the layout.")
    names = [f["name"] for f in frames if f["type"] == "tool_start"]
    print("tools:", names)
    text = "".join(f.get("content", "") for f in frames if f["type"] == "content")
    print("reply:", text[:500])
    assert "view_pages" in names
    assert frames[-1]["type"] == "complete"
    assert len(text) > 40


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="image generation uses OpenRouter")
def test_sketch_becomes_an_image(server):
    """The agent looks at the author's sketch and turns it into an image suggestion."""
    import io

    from PIL import Image, ImageDraw

    c = httpx.Client(base_url=server, timeout=120)
    did = c.post("/api/docs", json={"title": "Sketch to image"}).json()["id"]
    img = Image.new("RGB", (480, 300), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([60, 160, 200, 260], outline="black", width=4)       # a house
    d.polygon([(50, 160), (130, 90), (210, 160)], outline="black", width=4)
    d.ellipse([340, 40, 420, 120], outline="black", width=4)         # a sun
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    up = c.post(f"/api/docs/{did}/uploads", files={"file": ("s.png", buf.getvalue(), "image/png")},
                data={"purpose": "asset"}).json()
    b = c.post(f"/api/docs/{did}/blocks", json={"type": "image", "content": {"sketch": up["id"]}}).json()
    frames = chat(c, did, f"Turn the sketch on image block {b['id']} into a finished image. Look at it first.")
    names = [f["name"] for f in frames if f["type"] == "tool_start"]
    print("tools:", names)
    assert "view_sketch" in names and "generate_image" in names
    sugg = [s for s in c.get(f"/api/docs/{did}").json()["suggestions"] if s["block_id"] == b["id"]]
    assert sugg and sugg[0]["payload"]["content"]["src"].startswith("upl_")
