"""Checkpoints in a real browser (gh #61): what changed since a point in time, and going
back to it, with the pages after a restore identical to the pages before the change.

The author names a checkpoint in Document → Checkpoints, the list says how much changed
since, "changes since…" shows each changed block with the suggestion diff, one block can be
reverted or everything restored. An Accept all can be undone as a whole from the Review tab.
A restore touches content only, so the exported pages after it match the checkpoint's.
"""
from __future__ import annotations

import io
import time
import zipfile

from conftest import api  # noqa: E402
from PIL import Image, ImageChops

from langstage_codraft.tools import (
    begin_turn,
    propose_delete,
    propose_edit,
    propose_insert,
    propose_move,
)

BODY = ("Agents increasingly produce documents rather than chat replies, yet most hosts still treat their "
        "output as a transcript. A document should be co-authored: the person owns the argument and the agent "
        "owns the presentation. ")


def _cfg(doc_id: str) -> dict:
    return {"configurable": {"thread_id": doc_id}}


def _doc(c, title="Quarterly review"):
    did = c.post("/api/docs", json={"title": title, "template": "report"}).json()["id"]
    ids = []
    for i in range(4):
        ids.append(c.post(f"/api/docs/{did}/blocks", json={"type": "heading", "content": {"text": f"Part {i + 1}", "level": 2}}).json()["id"])
        ids.append(c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": f"Section {i + 1}. " + BODY * 3}}).json()["id"])
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


def _content(c, did):
    return [(b["id"], b["content"]) for b in c.get(f"/api/docs/{did}").json()["blocks"]]


def _address_comments(did, ids):
    """What an "address all comments" turn does: edits, an insert, a delete and a move, as suggestions."""
    cfg = _cfg(did)
    begin_turn(did, f"turn-{time.time()}")
    propose_edit.func(block_id=ids[1], content="Section 1, rewritten much shorter after the review.", config=cfg)
    propose_edit.func(block_id=ids[3], content="Section 2. " + BODY * 2 + "A closing sentence the reviewer asked for.", config=cfg)
    propose_insert.func(block_type="callout", content="Decision needed by Friday.", after_block_id=ids[3], config=cfg)
    propose_delete.func(block_id=ids[5], config=cfg)
    propose_move.func(block_id=ids[7], after_block_id=ids[1], config=cfg)


def test_undoing_an_accept_all_gives_back_the_same_pages_pixel_for_pixel(server):
    c = api(server)
    did, ids = _doc(c)
    original = _pages(c, did)
    before = _content(c, did)
    named = c.post(f"/api/docs/{did}/checkpoints", json={"name": "Sent to legal"}).json()
    _address_comments(did, ids)
    got = c.post(f"/api/docs/{did}/suggestions/accept-all", json={}).json()
    assert got["count"] == 5 and got["checkpoint"]
    accepted = _pages(c, did)
    assert not _same(original, accepted)  # the turn really changed the pages
    after = _content(c, did)
    # Both checkpoints describe the same moment, so they list the same changes.
    for cid in (named["id"], got["checkpoint"]):
        ch = c.get(f"/api/docs/{did}/checkpoints/{cid}/changes").json()
        assert ch["counts"] == {"edited": 2, "added": 1, "deleted": 1, "moved": 1, "total": 5}
    # Undo the Accept all: the content and the pages are the ones the checkpoint saw.
    r = c.post(f"/api/docs/{did}/checkpoints/{got['checkpoint']}/restore", json={}).json()["result"]
    assert r["changed"] and (r["edited"], r["restored"], r["removed"], r["moved"]) == (2, 1, 1, 1)
    assert _content(c, did) == before
    assert _same(original, _pages(c, did))
    # And the undo can be undone: the accepted document is back, pixel for pixel.
    c.post(f"/api/docs/{did}/checkpoints/{r['undo']}/restore", json={})
    assert _content(c, did) == after
    assert _same(accepted, _pages(c, did))
    # Much later the named checkpoint still takes it all back.
    c.post(f"/api/docs/{did}/checkpoints/{named['id']}/restore", json={})
    assert _content(c, did) == before and _same(original, _pages(c, did))


def test_the_document_tab_names_a_checkpoint_shows_what_changed_and_goes_back(page, server):
    c = api(server)
    did, ids = _doc(c, "Checkpoints in the editor")
    before = _content(c, did)
    page.goto(f"{server}/d/{did}")
    page.click("[data-testid=tab-document]")
    page.wait_for_selector("[data-testid=checkpoints-empty]")
    page.fill("[data-testid=checkpoint-name]", "Sent to legal")
    page.click("[data-testid=checkpoint-save]")
    page.wait_for_selector("[data-testid=checkpoint]")
    row = page.locator("[data-testid=checkpoint]").first
    assert "Sent to legal" in row.inner_text() and row.get_attribute("data-kind") == "named"
    assert row.locator("[data-testid=checkpoint-unchanged]").count() == 1
    # The agent's turn is accepted wholesale, and the author edits a block as well.
    _address_comments(did, ids)
    c.post(f"/api/docs/{did}/suggestions/accept-all", json={})
    c.patch(f"/api/blocks/{ids[0]}", json={"content": {"text": "Part one, retitled"}})
    named = page.locator("[data-testid=checkpoint][data-kind=named]")
    named.locator("[data-testid=checkpoint-changes]").wait_for()
    assert named.locator("[data-testid=checkpoint-changes]").inner_text() == "6 changes since…"
    auto = page.locator("[data-testid=checkpoint][data-reason=accept-all]")
    assert "Before Accept all (5 suggestions)" in auto.inner_text() and "6 changes since…" in auto.inner_text()
    # Changes since the named checkpoint: a card per changed block.
    named.locator("[data-testid=checkpoint-changes]").click()
    page.wait_for_selector("[data-testid=checkpoint-review]")
    assert "Changes since “Sent to legal”" in page.locator("[data-testid=checkpoint-review-title]").inner_text()
    assert "3 edited, 1 added, 1 deleted, 1 moved" in page.locator("[data-testid=checkpoint-review-summary]").inner_text()
    cards = page.locator("[data-testid=checkpoint-card]")
    assert cards.count() == 6
    edited = page.locator(f"[data-testid=checkpoint-card][data-block-id={ids[1]}]")
    assert edited.locator(".diff-add").count() >= 1 and edited.locator(".diff-del").count() >= 1
    assert "rewritten much shorter" in edited.inner_text()
    deleted = page.locator(f"[data-testid=checkpoint-card][data-block-id={ids[5]}]")
    assert deleted.get_attribute("data-kinds") == "deleted" and deleted.locator(".diff-del").count() == 1
    assert "Moved up" in page.locator(f"[data-testid=checkpoint-card][data-block-id={ids[7]}]").inner_text()
    # Revert one block: only that block goes back.
    edited.locator("[data-testid=checkpoint-revert]").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=checkpoint-card]').length === 5")
    now = dict(_content(c, did))
    assert now[ids[1]] == dict(before)[ids[1]] and now[ids[0]]["text"] == "Part one, retitled"
    assert ids[5] not in now
    # Restore all: the document is the one that was sent, and the toast offers to undo it.
    page.click("[data-testid=checkpoint-restore-all]")
    page.wait_for_function("document.querySelectorAll('[data-testid=checkpoint-card]').length === 0")
    assert _content(c, did) == before
    assert "Nothing changed" in page.locator("[data-testid=checkpoint-review-summary]").inner_text()
    toast = page.locator("[data-testid=toast]").last
    assert "Restored:" in toast.inner_text() and "Nothing is lost" in toast.inner_text()
    toast.locator("[data-testid=toast-action]").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=checkpoint-card]').length === 5")
    assert dict(_content(c, did))[ids[0]]["text"] == "Part one, retitled"
    # Back to the list: the restore left a checkpoint of its own; naming an automatic one keeps it.
    page.click("[data-testid=checkpoint-back]")
    page.wait_for_selector("[data-testid=checkpoint-list]")
    assert page.locator("[data-testid=checkpoint][data-reason=restore]").count() >= 1
    auto = page.locator("[data-testid=checkpoint][data-reason=accept-all]")
    auto.locator("[data-testid=checkpoint-rename]").click()
    page.fill("[data-testid=checkpoint-rename-input]", "Before the review round")
    page.click("[data-testid=checkpoint-rename-save]")
    page.wait_for_function(
        "[...document.querySelectorAll('[data-testid=checkpoint][data-kind=named]')]"
        ".some(e => e.innerText.includes('Before the review round'))")
    rows = c.get(f"/api/docs/{did}/checkpoints").json()["checkpoints"]
    assert {"Sent to legal", "Before the review round"} <= {r["name"] for r in rows if r["kind"] == "named"}


def test_the_review_tab_undoes_an_accept_all(page, server):
    c = api(server)
    did, ids = _doc(c, "Undo accept all")
    before = _content(c, did)
    _address_comments(did, ids)
    page.goto(f"{server}/d/{did}")
    page.click("[data-testid=tab-review]")
    page.wait_for_function("document.querySelectorAll('[data-testid=review-card]').length === 5")
    assert page.locator("[data-testid=last-accept-all]").count() == 0
    page.click("[data-testid=accept-all]")
    toast = page.locator("[data-testid=toast]").last
    toast.wait_for()
    assert "Accepted all 5." in toast.inner_text()
    assert toast.locator("[data-testid=toast-action]").inner_text() == "Undo accept all"
    assert _content(c, did) != before
    # The toast goes away; the Review tab keeps the way back.
    row = page.locator("[data-testid=last-accept-all]")
    row.wait_for()
    assert "2 edited, 1 added, 1 deleted, 1 moved" in row.inner_text()
    page.click("[data-testid=undo-accept-all]")
    page.wait_for_function("document.querySelectorAll('[data-testid=last-accept-all]').length === 0")
    assert _content(c, did) == before
    done = page.locator("[data-testid=toast]").last
    assert "Accept all undone:" in done.inner_text()
    # Its Undo puts the accepted text back.
    done.locator("[data-testid=toast-action]").click()
    page.wait_for_function(
        f"fetch('/api/docs/{did}').then(r => r.json()).then(d => d.blocks.some(b => (b.content.markdown || '').includes('rewritten much shorter')))")
    # "See changes" opens the same review the Document tab has.
    page.locator("[data-testid=last-accept-all]").wait_for()
    page.click("[data-testid=review-accept-all-changes]")
    page.wait_for_selector("[data-testid=checkpoint-review]")
    assert page.locator("[data-testid=checkpoint-card]").count() == 5
