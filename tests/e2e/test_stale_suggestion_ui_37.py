"""gh #37: a suggestion whose block the author edited afterwards is marked outdated in the
Review panel, diffed against the text the agent saw, and only applied with "Accept anyway"."""
from __future__ import annotations

import time

from conftest import api  # noqa: E402

from langstage_codraft.store import get_store

ORIG = "First. Second. Third. Fourth."
NEWER = ORIG + " My important new conclusion."


def test_outdated_suggestion_is_flagged_and_accept_all_skips_it(page, server):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Stale"}).json()["id"]
    a = c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": ORIG}}).json()
    o = c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Other."}}).json()
    stale = get_store().propose(did, "edit", {"content": {"markdown": "First. Second."}}, block_id=a["id"])
    get_store().propose(did, "edit", {"content": {"markdown": "Other, edited."}}, block_id=o["id"])
    c.patch(f"/api/blocks/{a['id']}", json={"content": {"markdown": NEWER}, "base": a["head"]})

    page.goto(f"{server}/d/{did}")
    page.wait_for_selector(".cd-page")
    page.click("[data-testid=tab-review]")
    page.locator("[data-testid=suggestion-outdated]").first.wait_for()
    card = page.locator("[data-testid=review-card]").filter(has=page.locator("[data-testid=suggestion-outdated]"))
    # The diff is against what the agent saw, so it doesn't show the author's sentence as deleted.
    assert "My important new conclusion" not in card.inner_text()
    assert card.locator("[data-testid=review-accept]").inner_text().strip() == "Accept anyway"

    page.get_by_role("button", name="Accept all").click()
    page.get_by_text("skipped 1 outdated suggestion").wait_for()
    assert get_store().get_block(a["id"])["content"]["markdown"] == NEWER
    assert get_store().get_suggestion(stale["id"])["status"] == "pending"

    card.locator("[data-testid=review-accept]").click()
    for _ in range(50):
        if get_store().get_suggestion(stale["id"])["status"] == "accepted":
            break
        time.sleep(0.1)
    assert get_store().get_suggestion(stale["id"])["payload"]["result"]["branched"] is True
