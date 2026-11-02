"""gh #34: a block or pending suggestion stored with a wrong-typed field (by a version before
the write check) blanked the whole editor, so Reject was unreachable, and exports hung 30 s."""
from __future__ import annotations

import json
import time

from conftest import api  # noqa: E402

from langstage_codraft.store import get_store


def _poke(sql: str, *args) -> None:
    s = get_store()
    with s._lock:
        s._db.execute(sql, args)
        s._db.commit()


def _bad_doc(c) -> tuple[str, str]:
    did = c.post("/api/docs", json={"title": "Tides"}).json()["id"]
    t = c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Body"}}).json()
    h = c.post(f"/api/docs/{did}/blocks", json={"type": "heading", "content": {"text": "Title"}}).json()
    _poke("UPDATE versions SET content = ? WHERE id = ?", json.dumps({"markdown": 42}), t["head"])
    _poke("UPDATE versions SET content = ? WHERE id = ?", json.dumps({"text": 5, "level": 9}), h["head"])
    s = c.post(f"/api/docs/{did}/blocks", json={"type": "callout", "content": {"markdown": "ok"}}).json()
    sg = get_store().propose(did, "insert", {"type": "callout", "content": {"markdown": "x"}, "after": s["id"]})
    _poke("UPDATE suggestions SET payload = ? WHERE id = ?",
          json.dumps({"type": "callout", "content": {"markdown": ["Note one", "Note two"]}, "after": s["id"]}),
          sg["id"])
    return did, sg["id"]


def test_the_editor_still_opens_and_reject_is_reachable(page, server):
    c = api(server)
    did, sid = _bad_doc(c)
    page.goto(f"{server}/d/{did}")
    page.wait_for_selector(".cd-page")
    assert "Body" not in page.locator(".cd-page").first.inner_text()  # 42 is read back as text
    assert "42" in page.locator(".cd-page").first.inner_text()
    page.click("[data-testid=tab-review]")
    page.locator("[data-testid=review-card]").first.wait_for()
    page.click("[data-testid=review-reject]")
    for _ in range(50):
        if get_store().get_suggestion(sid)["status"] == "rejected":
            break
        time.sleep(0.1)
    assert get_store().get_suggestion(sid)["status"] == "rejected"


def test_export_of_a_doc_with_old_bad_values_works(server):
    c = api(server)
    did, _ = _bad_doc(c)
    t0 = time.monotonic()
    r = c.get(f"/api/docs/{did}/export", params={"format": "pdf"})
    assert r.status_code == 200, r.text
    assert time.monotonic() - t0 < 25
    assert r.content[:4] == b"%PDF"
