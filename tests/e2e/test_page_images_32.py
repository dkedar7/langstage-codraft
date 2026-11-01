"""gh #32: on A4/A5 the pages sit at fractional CSS px, and every other page image began
with a sliver of the page above (a navy line under a dark cover) and was 2 px taller."""
from __future__ import annotations

import io
import zipfile

import pytest
from conftest import api, page_image  # noqa: E402

NAVY = "#002f58"
TEXT = "Lorem ipsum dolor sit amet. " * 25


def _doc(c, size: str) -> str:
    did = c.post("/api/docs", json={"title": f"Sliver {size}", "template": "report"}).json()["id"]
    c.patch(f"/api/docs/{did}", json={"settings": {"page_size": size, "cover": {"enabled": True, "background": NAVY}}})
    for _ in range(14):
        c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": TEXT}})
    return did


def _images(c, did: str, fmt: str):
    from PIL import Image

    r = c.get(f"/api/docs/{did}/export", params={"format": fmt})
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return [Image.open(io.BytesIO(z.read(n))).convert("RGB") for n in sorted(z.namelist())]


@pytest.mark.parametrize(("size", "fmt"), [("a4", "png"), ("a5", "png"), ("a4", "jpg"), ("letter", "png")])
def test_every_page_image_is_exactly_its_page(server, size, fmt):
    c = api(server)
    ims = _images(c, _doc(c, size), fmt)
    assert len(ims) >= 4
    assert len({im.size for im in ims}) == 1, [im.size for im in ims]
    for n, im in enumerate(ims[1:], 2):
        # body pages are white at the top: no row of the cover (or of any page above) bleeds in
        top = [im.getpixel((im.width // 2, y)) for y in range(3)]
        assert all(min(px) > 230 for px in top), (n, top)


def test_agent_view_pages_match_the_page_size(server):
    """The agent's view_pages screenshots come from the same capture: no sliver either."""
    from PIL import Image

    from langstage_codraft.render import Renderer

    c = api(server)
    did = _doc(c, "a4")
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        r = Renderer(server, Path(td))
        try:
            shots = r.call(r.screenshot_pages(did, pages=[1, 2, 3, 4]))
            ims = [Image.open(s["path"]).convert("RGB") for s in shots]
        finally:
            r.close()
        assert len({im.size for im in ims}) == 1, [im.size for im in ims]
        top = [ims[1].getpixel((ims[1].width // 2, y)) for y in range(2)]
        assert all(min(px) > 220 for px in top), top



@pytest.mark.parametrize("size", ["a4", "letter"])
def test_editor_matches_export_pixels_on_page_2(page, server, size):
    """The parity gate in test_ui.py only checks page 1 on Letter; A4 page 2 is where the
    fractional offset showed."""
    from PIL import ImageChops

    c = api(server)
    did = c.post("/api/docs", json={"title": f"Parity page 2 {size}"}).json()["id"]
    c.patch(f"/api/docs/{did}", json={"settings": {"page_size": size}})
    for _ in range(14):
        c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": TEXT}})
    exported = _images(c, did, "png")[1]
    ctx = page.context.browser.new_context(viewport={"width": 1800, "height": 1400}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(f"{server}/d/{did}")
    pg.wait_for_selector(".cd-page[data-page='2']")
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(1500)
    while pg.locator("text=100%").count() == 0:
        label = pg.locator(".w-10.text-center").inner_text()
        pg.click("[title='Zoom in']" if int(label.rstrip('%')) < 100 else "[title='Zoom out']")
    pg.wait_for_timeout(500)
    pg.mouse.move(0, 0)
    editor = page_image(pg, ".cd-page[data-page='2']")
    ctx.close()
    assert editor.size == exported.size, (editor.size, exported.size)
    diff = ImageChops.difference(editor, exported).convert("L")
    changed = sum(1 for v in diff.tobytes() if v > 48)
    ratio = changed / (diff.size[0] * diff.size[1])
    assert ratio < 0.01, f"{ratio:.4%} of pixels differ between editor and export on page 2"
