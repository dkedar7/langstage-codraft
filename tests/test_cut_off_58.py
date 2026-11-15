"""A layout change by the agent never hides content, and an export says what it cut off (gh #58).

``set_block_layout(height_px=20)`` on the author's 40-sentence paragraph left 3 sentences
in every export, with no suggestion, no version and no warning from ``export``. The tool
now refuses a box that cuts the block's content off, and the export route, the CLI and
``export_document`` name the blocks an export cuts off (``--strict`` / ``strict=True``
refuse the export).

The measurements themselves need Chromium: tests/e2e/test_cut_off_58.py.
"""
from __future__ import annotations

import io
import json
import warnings

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from PIL import Image

from langstage_codraft import cli, render, standalone
from langstage_codraft import server as server_mod
from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.mcp_server import build_mcp_server
from langstage_codraft.server import create_app, cut_off_message
from langstage_codraft.standalone import CutOffWarning, ExportError, cut_off_of
from langstage_codraft.store import Store
from langstage_codraft.tools import (
    cut_off_blocks,
    propose_insert,
    set_block_layout,
    set_document_style,
    set_typography,
)

LONG = " ".join(f"Sentence{i} is the author's own claim." for i in range(40))


def _doc(store, markdown=LONG):
    d = store.create_document("Clip")
    b = store.insert_block(d["id"], "text", {"markdown": markdown}, author="author")
    return d, b


class FakeRenderer:
    """Stands in for Chromium: ``cut`` says which blocks the print view would cut off."""

    def __init__(self, cut):
        self.cut, self.calls = cut, 0

    def call(self, value):
        return value

    def cut_off(self, doc_id, preview=False):
        self.calls += 1
        got = self.cut(doc_id) if callable(self.cut) else self.cut
        return [{"block_id": b, "page": 1, "type": "text", "natural_px": 560, "box_px": 20} for b in got]


@pytest.fixture
def renderer(monkeypatch):
    def use(cut):
        r = FakeRenderer(cut)
        monkeypatch.setattr(render, "get_renderer", lambda: r)
        return r

    return use


# ------------------------------------------------------------- the agent's tool
def test_a_height_that_cuts_the_text_off_is_refused_and_nothing_changes(store, cfg_for, renderer):
    d, b = _doc(store)
    before = store.get_block(b["id"])
    r = renderer(lambda doc_id: [b["id"]] if store.get_block(b["id"])["layout"]["height"] else [])
    out = set_block_layout.func(block_id=b["id"], height_px=20, config=cfg_for(d["id"]))
    assert out.startswith("ERROR: REFUSED, nothing changed") and b["id"] in out
    assert "needs about 560 px" in out and "auto_height=True or a height of at least 560 px" in out
    assert "propose_edit" in out  # shortening is a content change, for the author to approve
    after = store.get_block(b["id"])
    assert after["layout"] == before["layout"] and after["layout"]["height"] is None
    assert store.list_suggestions(d["id"]) == [] and r.calls == 1


def test_a_height_the_content_fits_is_applied(store, cfg_for, renderer):
    d, b = _doc(store)
    renderer([])
    out = set_block_layout.func(block_id=b["id"], height_px=600, config=cfg_for(d["id"]))
    assert out.startswith(f"Layout of {b['id']} is now") and store.get_block(b["id"])["layout"]["height"] == 600


def test_the_refusal_puts_the_whole_earlier_layout_back(store, cfg_for, renderer):
    d, b = _doc(store)
    store.set_layout(b["id"], {"span": "full", "variant": "lead", "dropcap": True})
    before = store.get_block(b["id"])["layout"]
    renderer(lambda doc_id: [b["id"]] if store.get_block(b["id"])["layout"]["pin"] else [])
    out = set_block_layout.func(block_id=b["id"], pin_page=1, page_x_px=40, page_y_px=40, page_w_px=300, page_h_px=30,
                                variant="small", config=cfg_for(d["id"]))
    assert out.startswith("ERROR: REFUSED") and "box of 30 px" in out
    assert store.get_block(b["id"])["layout"] == before


def test_without_a_renderer_only_a_plainly_too_small_box_is_refused(store, cfg_for):
    d, b = _doc(store)  # 280 words
    assert cut_off_blocks(d["id"]) is None  # nothing to measure with
    out = set_block_layout.func(block_id=b["id"], height_px=20, config=cfg_for(d["id"]))
    assert out.startswith("ERROR: REFUSED") and "has 280" in out and "would not print" in out
    assert store.get_block(b["id"])["layout"]["height"] is None
    # a box near the right size can't be judged without a browser: it is applied
    ok = set_block_layout.func(block_id=b["id"], height_px=400, config=cfg_for(d["id"]))
    assert ok.startswith("Layout of") and store.get_block(b["id"])["layout"]["height"] == 400


def test_a_restyle_of_a_block_the_author_already_boxed_is_not_refused(store, cfg_for, renderer):
    d, b = _doc(store)
    store.set_layout(b["id"], {"height": 40})  # the author's own choice, in the Block panel
    r = renderer([b["id"]])  # cut off before and after
    out = set_block_layout.func(block_id=b["id"], variant="small", config=cfg_for(d["id"]))
    assert out.startswith("Layout of") and store.get_block(b["id"])["layout"]["variant"] == "small"
    assert r.calls == 2  # once after the change, once with the old layout back
    # but the agent can't make the box smaller still
    out = set_block_layout.func(block_id=b["id"], height_px=24, config=cfg_for(d["id"]))
    assert out.startswith("ERROR: REFUSED") and store.get_block(b["id"])["layout"]["height"] == 40


def test_a_restyle_that_makes_a_fitting_box_overflow_is_refused(store, cfg_for, renderer):
    d, b = _doc(store, "A short note.")
    store.set_layout(b["id"], {"height": 40})
    renderer(lambda doc_id: [b["id"]] if store.get_block(b["id"])["layout"]["variant"] == "pullquote" else [])
    out = set_block_layout.func(block_id=b["id"], variant="pullquote", config=cfg_for(d["id"]))
    assert out.startswith("ERROR: REFUSED") and store.get_block(b["id"])["layout"]["variant"] is None


def test_pictures_and_blocks_without_a_box_are_not_measured(store, cfg_for, renderer):
    d, b = _doc(store)
    img = store.insert_block(d["id"], "image", {"src": "https://example.org/a.png"}, author="author")
    r = renderer([b["id"], img["id"]])
    assert set_block_layout.func(block_id=img["id"], height_px=40, config=cfg_for(d["id"])).startswith("Layout of")
    assert set_block_layout.func(block_id=b["id"], full_width=True, config=cfg_for(d["id"])).startswith("Layout of")
    assert set_block_layout.func(block_id=b["id"], auto_height=True, config=cfg_for(d["id"])).startswith("Layout of")
    assert r.calls == 0


def test_a_pending_insert_keeps_its_layout_in_the_suggestion(store, cfg_for, renderer):
    d, b = _doc(store)
    r = renderer([b["id"]])
    out = propose_insert.func(block_type="text", content="A new paragraph.", config=cfg_for(d["id"]))
    sug = store.list_suggestions(d["id"])[0]
    out = set_block_layout.func(block_id=sug["id"], height_px=20, config=cfg_for(d["id"]))
    assert "pending insert" in out and r.calls == 0  # the author sees it and decides


def test_a_document_wide_restyle_names_the_blocks_it_leaves_cut_off(store, cfg_for, renderer):
    d, b = _doc(store)
    r = renderer([b["id"]])
    # nothing has a fixed box: nothing to render for
    assert "OVERFLOW" not in set_typography.func(font_size_px=18, config=cfg_for(d["id"]))
    assert r.calls == 0
    store.set_layout(b["id"], {"height": 300})
    out = set_typography.func(font_size_px=22, config=cfg_for(d["id"]))
    assert f"OVERFLOW: {b['id']} (page 1: its content needs about 560 px, the box is 20 px)" in out
    assert "auto_height=True" in out
    assert "OVERFLOW" in set_document_style.func(columns=3, config=cfg_for(d["id"]))
    r.cut = []
    assert "OVERFLOW" not in set_document_style.func(columns=1, config=cfg_for(d["id"]))


async def test_an_mcp_client_gets_the_same_refusal(store, renderer):
    d, b = _doc(store)
    renderer(lambda doc_id: [b["id"]] if store.get_block(b["id"])["layout"]["height"] else [])
    server = build_mcp_server()
    res = await server.call_tool("set_block_layout", {"doc_id": d["id"], "block_id": b["id"], "height_px": 20})
    content = res[0] if isinstance(res, tuple) else getattr(res, "content", res)
    text = "\n".join(getattr(c, "text", "") for c in content)
    assert "REFUSED, nothing changed" in text and store.get_block(b["id"])["layout"]["height"] is None
    tools = {t.name: t for t in await server.list_tools()}
    assert "is refused" in tools["set_block_layout"].description  # the rule is in what an MCP client reads


def test_set_layout_replace_sets_exactly_the_layout_given(store):
    d, b = _doc(store)
    first = store.get_block(b["id"])["layout"]
    store.set_layout(b["id"], {"span": "full", "height": 80, "pin": {"anchor": "page", "page": 2, "x": 1, "y": 2,
                                                                      "w": 300, "h": 90}, "repeat": "all"})
    assert store.set_layout(b["id"], first, replace=True)["layout"] == first


# ----------------------------------------------------------------- the export
class ExportRenderer:
    """A renderer whose outputs report ``cut`` the way the real one does (``info``)."""

    def __init__(self, cut, real=None):
        self.cut, self.real = cut, real

    async def acall(self, value):
        return value

    def _note(self, info):
        if info is not None:
            info["cut_off"] = [dict(c) for c in self.cut]

    def pdf(self, doc_id, info=None):
        self._note(info)
        return b"%PDF-fake"

    def page_images(self, doc_id, fmt="png", scale=2.0, pages=None, quality=85, info=None):
        self._note(info)
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(buf, "PNG")
        return [(1, buf.getvalue())]

    def html_zip(self, doc_id, title="document", info=None):
        self._note(info)
        return b"zip"

    def structure(self, doc_id, scale=2.0, info=None):
        self._note(info)
        return {"page_px": (816, 1056), "pages": []}

    def cut_off(self, doc_id, preview=False):
        return [dict(c) for c in self.cut]

    def close(self):  # the app closes "its" renderer on shutdown: pass it on to the real one
        if self.real is not None:
            self.real.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    store = Store(tmp_path / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []}, store=store)
    with TestClient(app) as c:
        c.store = store
        yield c


def _cut_doc(client, monkeypatch, cut=True):
    did = client.post("/api/docs", json={"title": "Clip"}).json()["id"]
    b = client.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": LONG}}).json()
    rec = [{"block_id": b["id"], "page": 2, "type": "text", "natural_px": 560, "box_px": 20}] if cut else []
    fake = ExportRenderer(rec, server_mod.get_renderer())
    monkeypatch.setattr(server_mod, "get_renderer", lambda: fake)
    return did, b["id"]


@pytest.mark.parametrize("fmt", ["pdf", "png", "jpg", "html", "gif", "pptx"])
def test_every_paged_export_says_which_blocks_it_cut_off(client, monkeypatch, fmt):
    did, bid = _cut_doc(client, monkeypatch)
    r = client.get(f"/api/docs/{did}/export", params={"format": fmt})
    assert r.status_code == 200, r.text
    assert r.headers["x-codraft-cut-off"] == f"{bid}@2"
    assert cut_off_of(r) == [{"block_id": bid, "page": 2}]


def test_an_export_with_nothing_cut_off_has_no_header_and_word_never_does(client, monkeypatch):
    did, _ = _cut_doc(client, monkeypatch, cut=False)
    r = client.get(f"/api/docs/{did}/export", params={"format": "pdf"})
    assert r.status_code == 200 and "x-codraft-cut-off" not in r.headers and cut_off_of(r) == []
    did, _ = _cut_doc(client, monkeypatch)
    r = client.get(f"/api/docs/{did}/export", params={"format": "docx"})  # Word reflows the text itself
    assert r.status_code == 200 and "x-codraft-cut-off" not in r.headers


def test_strict_refuses_an_export_that_cuts_content_off(client, monkeypatch):
    did, bid = _cut_doc(client, monkeypatch)
    r = client.get(f"/api/docs/{did}/export", params={"format": "pdf", "strict": "true"})
    assert r.status_code == 409
    body = r.json()
    assert body["cut_off"] == [{"block_id": bid, "page": 2, "type": "text", "natural_px": 560, "box_px": 20,
                                "preview": body["cut_off"][0]["preview"]}]
    assert body["cut_off"][0]["preview"].startswith("Sentence0 is the author's own claim.")
    assert "1 block is cut off in this export" in body["detail"] and bid in body["detail"]
    assert "needs 560 px, has 20 px" in body["detail"]
    assert cut_off_of(r) == body["cut_off"]
    # nothing cut off: strict changes nothing
    did, _ = _cut_doc(client, monkeypatch, cut=False)
    assert client.get(f"/api/docs/{did}/export", params={"format": "pdf", "strict": "true"}).status_code == 200


def test_layout_check_lists_what_an_export_would_cut_off(client, monkeypatch):
    did, bid = _cut_doc(client, monkeypatch)
    got = client.get(f"/api/docs/{did}/layout-check").json()
    assert got["ok"] is False and [c["block_id"] for c in got["cut_off"]] == [bid]
    assert got["cut_off"][0]["natural_px"] == 560 and got["cut_off"][0]["type"] == "text"
    did, _ = _cut_doc(client, monkeypatch, cut=False)
    assert client.get(f"/api/docs/{did}/layout-check").json() == {"ok": True, "cut_off": []}
    assert client.get("/api/docs/doc_nope/layout-check").status_code == 404


def test_cut_off_message_counts_and_caps():
    one = cut_off_message([{"block_id": "blk_a", "page": 1}])
    assert one.startswith("1 block is cut off in this export, so part of its content does not print: blk_a (page 1)")
    many = cut_off_message([{"block_id": f"blk_{i}", "page": i} for i in range(11)])
    assert many.startswith("11 blocks are cut off") and "and 3 more" in many


# --------------------------------------------------------------------- the CLI
class CutStandalone:
    """The in-process app, for the CLI: doc_cut exports with one block cut off."""

    instances: list = []

    def __init__(self, data_dir=None, *, cfg=None, store=None):
        self.cfg, self.calls, self.closed = cfg, [], False
        CutStandalone.instances.append(self)

    def start(self):
        return self

    def close(self):
        self.closed = True

    def get(self, doc_id, fmt="pdf", strict=False):
        self.calls.append((doc_id, fmt, strict))
        cut = doc_id == "doc_cut"
        if cut and strict:
            rec = [{"block_id": "blk_long", "page": 1, "type": "text", "natural_px": 560, "box_px": 20,
                    "preview": "Sentence0 is"}]
            return httpx.Response(409, json={"detail": cut_off_message(rec), "cut_off": rec})
        headers = {"content-type": "application/pdf", "content-disposition": f'attachment; filename="{doc_id}.{fmt}"'}
        if cut:
            headers["x-codraft-cut-off"] = "blk_long@1"
        return httpx.Response(200, content=b"pdf", headers=headers)


@pytest.fixture
def fake(monkeypatch):
    CutStandalone.instances = []
    monkeypatch.setattr(standalone, "Standalone", CutStandalone)
    return CutStandalone


def test_export_warns_about_a_cut_off_block_and_still_writes_the_file(fake, tmp_path):
    out = tmp_path / "out.pdf"
    res = CliRunner().invoke(cli.main, ["export", "doc_cut", "-o", str(out), "--data-dir", str(tmp_path / "d")])
    assert res.exit_code == 0, res.output
    assert out.read_bytes() == b"pdf" and "[ ok ] wrote" in res.output
    assert "[warn] doc_cut: 1 block is cut off in out.pdf" in res.output
    assert "blk_long (page 1)" in res.output and "--strict" in res.output
    clean = CliRunner().invoke(cli.main, ["export", "doc_fine", "-o", str(out), "--data-dir", str(tmp_path / "d")])
    assert clean.exit_code == 0 and "[warn]" not in clean.output


def test_strict_export_fails_and_writes_nothing(fake, tmp_path):
    outdir = tmp_path / "out"
    res = CliRunner().invoke(cli.main, ["export", "doc_cut", "doc_fine", "-o", str(outdir), "--strict",
                                        "--data-dir", str(tmp_path / "d")])
    assert res.exit_code == 1, res.output
    assert "[fail] doc_cut (pdf): not written (--strict): 1 block is cut off" in res.output
    assert sorted(p.name for p in outdir.iterdir()) == ["doc_fine.pdf"]  # the clean one still exports
    assert fake.instances[0].calls == [("doc_cut", "pdf", True), ("doc_fine", "pdf", True)]


def test_json_export_reports_each_file_and_its_cut_off_blocks(fake, tmp_path):
    outdir = tmp_path / "out"
    res = CliRunner().invoke(cli.main, ["export", "doc_cut", "doc_fine", "-o", str(outdir), "--json",
                                        "--data-dir", str(tmp_path / "d")])
    assert res.exit_code == 0, res.output
    rows = [json.loads(line) for line in res.output.strip().splitlines()]
    assert [r["doc_id"] for r in rows] == ["doc_cut", "doc_fine"] and all(r["ok"] for r in rows)
    assert rows[0]["cut_off"] == [{"block_id": "blk_long", "page": 1}] and rows[1]["cut_off"] == []
    assert rows[0]["file"].endswith("doc_cut.pdf") and rows[0]["bytes"] == 3
    strict = CliRunner().invoke(cli.main, ["export", "doc_cut", "-o", str(outdir / "x.pdf"), "--json", "--strict",
                                           "--data-dir", str(tmp_path / "d")])
    assert strict.exit_code == 1
    row = json.loads(strict.stdout.strip().splitlines()[-1])
    assert row["ok"] is False and row["error"] == "cut_off" and row["cut_off"][0]["block_id"] == "blk_long"


# ------------------------------------------------------------------ from Python
def test_export_document_warns_and_strict_raises(monkeypatch, tmp_path):
    class SA(CutStandalone):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    monkeypatch.setattr(standalone, "Standalone", SA)
    with pytest.warns(CutOffWarning, match=r"doc_cut: 1 block\(s\) are cut off .* blk_long \(page 1\)"):
        assert standalone.export_document(tmp_path, "doc_cut", "pdf") == b"pdf"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert standalone.export_document(tmp_path, "doc_fine", "pdf") == b"pdf"
    out = tmp_path / "strict.pdf"
    with pytest.raises(ExportError) as err:
        standalone.export_document(tmp_path, "doc_cut", "pdf", out, strict=True)
    assert err.value.status == 409 and "cut off" in err.value.detail and not out.exists()


def test_public_names():
    import langstage_codraft

    assert langstage_codraft.CutOffWarning is CutOffWarning
