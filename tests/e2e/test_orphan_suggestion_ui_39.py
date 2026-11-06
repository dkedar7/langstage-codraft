"""gh #39: a pending suggestion whose block was deleted (left behind by an older version)
shows "Can't be applied" with only Dismiss, Accept all reports it, and Dismiss clears it."""
from __future__ import annotations

import time

from conftest import api  # noqa: E402

from langstage_codraft.store import get_store


def test_orphaned_suggestion_offers_only_dismiss(page, server):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Orphan"}).json()["id"]
    gone = c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Gone."}}).json()
    kept = c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Kept."}}).json()
    st = get_store()
    orphan = st.propose(did, "edit", {"content": {"markdown": "Gone, edited."}}, block_id=gone["id"])
    st.propose(did, "edit", {"content": {"markdown": "Kept, edited."}}, block_id=kept["id"])
    with st._lock:  # what 0.5.15 left behind: the block deleted, its suggestion still pending
        st._db.execute("UPDATE blocks SET deleted = 1 WHERE id = ?", (gone["id"],))
        st._db.commit()

    page.goto(f"{server}/d/{did}")
    page.wait_for_selector(".cd-page")
    page.click("[data-testid=tab-review]")
    page.locator("[data-testid=suggestion-gone]").wait_for()
    card = page.locator("[data-testid=review-card]").filter(has=page.locator("[data-testid=suggestion-gone]"))
    assert card.locator("[data-testid=review-accept]").count() == 0
    assert card.locator("[data-testid=review-reject]").inner_text().strip() == "Dismiss"

    page.get_by_role("button", name="Accept all").click()
    page.get_by_text("skipped 1 on deleted block").wait_for()
    assert st.get_block(kept["id"])["content"]["markdown"] == "Kept, edited."

    card.locator("[data-testid=review-reject]").click()
    for _ in range(50):
        if st.get_suggestion(orphan["id"])["status"] == "rejected":
            break
        time.sleep(0.1)
    assert st.get_suggestion(orphan["id"])["status"] == "rejected"
    page.locator("[data-testid=suggestion-gone]").wait_for(state="detached")
