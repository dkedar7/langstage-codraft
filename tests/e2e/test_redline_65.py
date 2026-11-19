"""Redline Word export in the editor (gh #65): a checkpoint's changes, and the pending
suggestions, download as Word files with tracked changes."""
from __future__ import annotations

import io
import zipfile

from conftest import api  # noqa: E402

from langstage_codraft.tools import propose_edit


def _revisions(path) -> tuple[int, int]:
    xml = zipfile.ZipFile(io.BytesIO(open(path, "rb").read())).read("word/document.xml").decode("utf-8")
    return xml.count("<w:ins "), xml.count("<w:del ")


def _doc(c):
    did = c.post("/api/docs", json={"title": "Brief"}).json()["id"]
    ids = [c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": f"Clause {i} as agreed."}}).json()["id"]
           for i in range(3)]
    return did, ids


def test_a_checkpoint_exports_a_redline_from_the_list_and_the_changes_view(page, server, tmp_path):
    c = api(server)
    did, ids = _doc(c)
    c.post(f"/api/docs/{did}/checkpoints", json={"name": "Sent to legal"})
    c.patch(f"/api/blocks/{ids[1]}", json={"content": {"markdown": "Clause 1 as changed after review."}})
    page.goto(f"{server}/d/{did}")
    page.click("[data-testid=tab-document]")
    named = page.locator("[data-testid=checkpoint][data-kind=named]")
    named.locator("[data-testid=checkpoint-redline]").wait_for()
    with page.expect_download() as dl:
        named.locator("[data-testid=checkpoint-redline]").click()
    assert dl.value.suggested_filename == "brief-redline.docx"
    out = tmp_path / "a.docx"
    dl.value.save_as(out)
    ins, dels = _revisions(out)
    assert ins >= 1 and dels >= 1
    # The changes view has the same button.
    named.locator("[data-testid=checkpoint-changes]").click()
    page.wait_for_selector("[data-testid=checkpoint-review]")
    with page.expect_download() as dl2:
        page.click("[data-testid=checkpoint-review-redline]")
    assert dl2.value.suggested_filename == "brief-redline.docx"


def test_the_export_menu_offers_both_redlines(page, server, tmp_path):
    c = api(server)
    did, ids = _doc(c)
    page.goto(f"{server}/d/{did}")
    page.click("[data-testid=export]")
    assert page.locator("[data-testid=export-docx-pending]").is_disabled()  # nothing pending yet
    # "Word: changes since…" leads to the checkpoints.
    page.click("[data-testid=export-docx-since]")
    page.wait_for_selector("[data-testid=checkpoints]")
    propose_edit.func(block_id=ids[0], content="Clause 0, suggested.", config={"configurable": {"thread_id": did}})
    page.reload()
    page.click("[data-testid=export]")
    pending = page.locator("[data-testid=export-docx-pending]")
    pending.wait_for()
    page.wait_for_function("!document.querySelector('[data-testid=export-docx-pending]').disabled")
    with page.expect_download() as dl:
        pending.click()
    assert dl.value.suggested_filename == "brief-suggestions.docx"
    out = tmp_path / "s.docx"
    dl.value.save_as(out)
    assert _revisions(out)[0] >= 1
