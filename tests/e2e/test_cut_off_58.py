"""Cut-off content, measured in a real browser (gh #58).

The agent's ``set_block_layout`` can't give a block a box that cuts its content off, an
export names the blocks it cuts off (and ``strict`` refuses it), and the editor tells the
author when a fixed height they chose leaves text out of the export.
"""
from __future__ import annotations

import io
import warnings
from pathlib import Path

import pytest
from conftest import api  # noqa: E402

from langstage_codraft import export_document
from langstage_codraft.standalone import CutOffWarning, ExportError, cut_off_of
from langstage_codraft.store import Store
from langstage_codraft.tools import set_block_layout, set_typography

LONG = " ".join(f"Sentence{i} is the author's own claim." for i in range(40))


def _cfg(doc_id: str) -> dict:
    return {"configurable": {"thread_id": doc_id}}


def _doc(c, title="Clip"):
    did = c.post("/api/docs", json={"title": title}).json()["id"]
    b = c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": LONG}}).json()
    return did, b["id"]


def _sentences_in_pdf(c, did) -> int:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(io.BytesIO(c.get(f"/api/docs/{did}/export", params={"format": "pdf"}).content))
    text = " ".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    text = " ".join(text.replace("­", "").replace("￾", "").split())
    return sum(f"Sentence{i} " in text for i in range(40))


def test_the_agent_cannot_shorten_what_the_document_prints_with_a_height(server):
    c = api(server)
    did, bid = _doc(c)
    assert _sentences_in_pdf(c, did) == 40
    out = set_block_layout.func(block_id=bid, height_px=20, config=_cfg(did))
    assert out.startswith("ERROR: REFUSED, nothing changed"), out
    need = int(out.split("needs about ")[1].split(" px")[0])
    assert need > 150 and f"a height of at least {need} px" in out
    doc = c.get(f"/api/docs/{did}").json()
    assert doc["blocks"][0]["layout"]["height"] is None and doc["suggestions"] == []
    assert _sentences_in_pdf(c, did) == 40
    assert c.get(f"/api/docs/{did}/layout-check").json() == {"ok": True, "cut_off": []}
    # the height the refusal names does fit, and so does a pin's box of that size
    assert set_block_layout.func(block_id=bid, height_px=need, config=_cfg(did)).startswith("Layout of")
    assert c.get(f"/api/docs/{did}/layout-check").json()["ok"] is True
    out = set_block_layout.func(block_id=bid, pin_page=1, page_x_px=80, page_y_px=80, page_w_px=600, page_h_px=60,
                                config=_cfg(did))
    assert out.startswith("ERROR: REFUSED") and "box of 60 px" in out
    assert c.get(f"/api/docs/{did}").json()["blocks"][0]["layout"]["pin"] is None
    # a restyle that makes the text outgrow the box it has is refused too
    out = set_block_layout.func(block_id=bid, variant="lead", config=_cfg(did))
    assert out.startswith("ERROR: REFUSED"), out
    assert c.get(f"/api/docs/{did}").json()["blocks"][0]["layout"]["variant"] is None
    # and a document-wide one says which block it left cut off
    note = set_typography.func(font_size_px=22, config=_cfg(did))
    assert f"OVERFLOW: {bid} (page 1: its content needs about" in note


def test_an_export_names_the_blocks_it_cuts_off_and_strict_refuses_it(server):
    c = api(server)
    did, bid = _doc(c)
    c.patch(f"/api/blocks/{bid}", json={"layout": {"height": 20}})  # the author, in the Block panel
    check = c.get(f"/api/docs/{did}/layout-check").json()
    assert check["ok"] is False and len(check["cut_off"]) == 1
    cut = check["cut_off"][0]
    assert cut["block_id"] == bid and cut["page"] == 1 and cut["type"] == "text" and cut["box_px"] == 20
    assert cut["natural_px"] > 150 and cut["preview"].startswith("Sentence0 is")
    for fmt in ("pdf", "png", "html", "pptx"):
        r = c.get(f"/api/docs/{did}/export", params={"format": fmt})
        assert r.status_code == 200 and r.headers["x-codraft-cut-off"] == f"{bid}@1", fmt
        assert cut_off_of(r) == [{"block_id": bid, "page": 1}]
    assert _sentences_in_pdf(c, did) < 10  # which is what the warning is about
    assert "x-codraft-cut-off" not in c.get(f"/api/docs/{did}/export", params={"format": "docx"}).headers
    r = c.get(f"/api/docs/{did}/export", params={"format": "pdf", "strict": "true"})
    assert r.status_code == 409 and r.json()["cut_off"][0]["block_id"] == bid
    assert "1 block is cut off in this export" in r.json()["detail"]
    # an agent restyling the block the author boxed is fine; making the box smaller is not
    assert set_block_layout.func(block_id=bid, full_width=True, config=_cfg(did)).startswith("Layout of")
    assert set_block_layout.func(block_id=bid, height_px=16, config=_cfg(did)).startswith("ERROR: REFUSED")
    # auto height: nothing is cut off, no header, strict passes
    c.patch(f"/api/blocks/{bid}", json={"layout": {"height": None}})
    r = c.get(f"/api/docs/{did}/export", params={"format": "pdf", "strict": "true"})
    assert r.status_code == 200 and "x-codraft-cut-off" not in r.headers
    assert _sentences_in_pdf(c, did) == 40


def test_the_editor_tells_the_author_and_fits_the_box_on_request(page, server):
    c = api(server)
    did, bid = _doc(c, "Clip in the editor")
    c.patch(f"/api/blocks/{bid}", json={"layout": {"height": 40}})
    page.goto(f"{server}/d/{did}")
    page.locator(f".cd-page [data-block-id={bid}]").first.click()
    page.click("[data-testid=tab-block]")
    warn = page.locator("[data-testid=cut-off-warning]")
    warn.wait_for()
    text = warn.inner_text()
    assert "Cut off." in text and "this box is 40 px" in text and "left out of every export" in text
    # Export still works, and says what is missing
    page.click("[data-testid=export]")
    with page.expect_download():
        page.click("[data-testid=export-pdf]")
    page.wait_for_function(
        "[...document.querySelectorAll('[data-testid=toast]')].some((t) => t.textContent.includes('cut off by a fixed height'))")
    # One click gives the block the height its content needs
    page.click("[data-testid=cut-off-fit]")
    page.wait_for_function("!document.querySelector('[data-testid=cut-off-warning]')")
    assert c.get(f"/api/docs/{did}/layout-check").json() == {"ok": True, "cut_off": []}
    assert c.get(f"/api/docs/{did}").json()["blocks"][0]["layout"]["height"] > 150


# Last in the module: Standalone is this process's Codraft app while it runs, and the `server`
# fixture above shares the process.
def test_export_document_warns_from_a_data_directory_and_strict_raises(tmp_path):
    d = tmp_path / ".codraft"
    s = Store(d / "codraft.db")
    doc = s.create_document("Clip")
    b = s.insert_block(doc["id"], "text", {"markdown": LONG})
    s.set_layout(b["id"], {"height": 24})
    s.close()
    with pytest.warns(CutOffWarning, match=b["id"]):
        pdf = export_document(d, doc["id"], "pdf")
    assert pdf[:4] == b"%PDF"
    out = tmp_path / "strict.pdf"
    with pytest.raises(ExportError) as err:
        export_document(d, doc["id"], "pdf", out, strict=True)
    assert err.value.status == 409 and b["id"] in err.value.detail and not out.exists()
    s = Store(d / "codraft.db")
    s.set_layout(b["id"], {"height": None})
    s.close()
    with warnings.catch_warnings():
        warnings.simplefilter("error", CutOffWarning)
        assert Path(export_document(d, doc["id"], "pdf", out, strict=True)).is_file()
