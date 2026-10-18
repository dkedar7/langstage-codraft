"""0.5 in a real browser: the cover switch, designed pages that export pixel for pixel, and
reading a document's look back out of its own PDF."""
from __future__ import annotations

import io
import zipfile

from conftest import api

LOREM = ("Water utilities lose a large share of treated water before it reaches a customer. Pressure "
         "management lowers losses from background leakage and reduces the rate of new bursts, which "
         "extends the life of ageing mains. See [the SWAN library](https://example.org/swan) for cases. ")

SWAN = {
    "page_size": "a4", "columns": 2,
    "margins": {"top": 20, "right": 18, "bottom": 22, "left": 18},
    "style": {"font_body": "'Raleway', sans-serif", "font_heading": "'Raleway', sans-serif",
              "color_text": "#575756", "color_heading": "#002f58", "color_accent": "#009fe3",
              "color_callout": "#e6f5fc", "font_size": 13.5},
    "cover": {"enabled": True, "background": "#002f58"},
    "footer": {"left": "{page}", "center": "{title}", "right": "SWAN Forum"},
}


def _designed(c, title="Pressure management pilot"):
    doc = c.post("/api/docs", json={"template": "report", "title": title, "settings": SWAN}).json()
    did = doc["id"]
    for b in doc["blocks"]:
        if b["type"] == "text" and b["layout"]["role"] == "flow":
            c.patch(f"/api/blocks/{b['id']}", json={"content": {"markdown": (LOREM * 4 + "\n\n") * 2}})
    findings = next(b for b in doc["blocks"] if b["type"] == "heading" and b["content"]["text"] == "Findings")
    c.post(f"/api/docs/{did}/blocks", json={"type": "callout", "after": findings["id"],
                                            "content": {"markdown": "**Key point.** Pressure management pays back in two years.",
                                                        "tone": "info"}})
    return did


def _editor_page(browser, server, did, n):
    from PIL import Image

    ctx = browser.new_context(viewport={"width": 1800, "height": 1400}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(f"{server}/d/{did}")
    pg.wait_for_selector(f".cd-page[data-page='{n}']")
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(1500)
    while pg.locator("text=100%").count() == 0:
        label = pg.locator(".w-10.text-center").inner_text()
        pg.click("[title='Zoom in']" if int(label.rstrip('%')) < 100 else "[title='Zoom out']")
    pg.wait_for_timeout(500)
    pg.mouse.move(0, 0)
    img = Image.open(io.BytesIO(pg.locator(f".cd-page[data-page='{n}']").screenshot())).convert("RGB")
    ctx.close()
    return img


def test_cover_switch_from_the_page_label(page, server):
    c = api(server)
    doc = c.post("/api/docs", json={"template": "report", "title": "Annual review"}).json()
    page.goto(f"{server}/d/{doc['id']}")
    page.wait_for_selector(".cd-page-cover")
    page.click("[data-testid=cover-off]")
    page.wait_for_selector("[data-testid=cover-on]")
    assert page.locator(".cd-page-cover").count() == 0
    first = page.locator(".cd-page").first.inner_text()
    assert first.index("Annual review") < first.index("Subtitle") < first.index("Executive summary")
    assert c.get(f"/api/docs/{doc['id']}").json()["settings"]["cover"]["enabled"] is False
    page.click("[data-testid=cover-on]")
    page.wait_for_selector(".cd-page-cover")
    page.click("[data-testid=cover-add]")
    page.keyboard.type("Example Forum, 2026")
    page.mouse.click(5, 400)
    page.wait_for_timeout(800)
    roles = [b["layout"]["role"] for b in c.get(f"/api/docs/{doc['id']}").json()["blocks"]]
    assert roles[:3] == ["cover", "cover", "cover"]


def test_designed_pages_export_pixel_for_pixel(browser, server):
    """Parity gate for the 0.5 page furniture: dark cover, own fonts and colours, footer."""
    from PIL import Image, ImageChops

    c = api(server)
    did = _designed(c, "Parity by design")
    exp = c.get(f"/api/docs/{did}/export", params={"format": "png"})
    assert exp.status_code == 200, exp.text
    z = zipfile.ZipFile(io.BytesIO(exp.content))
    names = sorted(z.namelist())
    for n in (1, 2):  # the cover and the first body page (with its footer)
        exported = Image.open(io.BytesIO(z.read(names[n - 1]))).convert("RGB")
        editor = _editor_page(browser, server, did, n)
        assert editor.size == exported.size, (n, editor.size, exported.size)
        diff = ImageChops.difference(editor, exported).convert("L")
        ratio = sum(1 for v in diff.tobytes() if v > 48) / (diff.size[0] * diff.size[1])
        assert ratio < 0.01, f"page {n}: {ratio:.4%} of pixels differ between editor and export"


def test_a_documents_look_survives_a_round_trip_through_its_pdf(server):
    """Export a SWAN-styled report, upload the PDF to a new document as a style, and read
    back its page setup, fonts, colours, cover and footer."""
    c = api(server)
    did = _designed(c)
    pdf = c.get(f"/api/docs/{did}/export", params={"format": "pdf"})
    assert pdf.status_code == 200
    target = c.post("/api/docs", json={"template": "report", "title": "Copycat"}).json()
    u = c.post(f"/api/docs/{target['id']}/uploads", data={"purpose": "style"},
               files={"file": ("swan.pdf", pdf.content, "application/pdf")}).json()
    assert len(u["pages"]) >= 3 and u["style"]["source"] == "pdf"
    r = c.post(f"/api/uploads/{u['id']}/apply-style").json()
    s = r["doc"]["settings"]
    assert s["page_size"] == "a4" and s["columns"] == 2, r["changes"]
    for k, want in SWAN["margins"].items():
        assert abs(s["margins"][k] - want) <= 4, (k, s["margins"], r["changes"])
    st = s["style"]
    assert st["font_body"].startswith("'Raleway'") and st["font_heading"].startswith("'Raleway'"), st
    assert (st["color_text"], st["color_heading"], st["color_accent"]) == ("#575756", "#002f58", "#009fe3"), st
    assert st["color_callout"] == "#e6f5fc" and abs(st["font_size"] - 13.5) <= 0.5, st
    assert s["cover"]["enabled"] and s["cover"]["background"] == "#002f58" and s["cover"]["text"] == "light"
    assert (s["footer"]["left"], s["footer"]["right"]) == ("{page}", "SWAN Forum"), s["footer"]


def test_design_tab_matches_an_uploaded_word_file(page, server, tmp_path):
    import docx
    from docx.shared import Pt, RGBColor

    d = docx.Document()
    d.styles["Normal"].font.name = "Calibri"
    d.styles["Normal"].font.size = Pt(11)
    h = d.styles["Heading 1"]
    h.font.name, h.font.color.rgb = "Georgia", RGBColor(0x7A, 0x1F, 0x3D)
    d.add_heading("Brand guide", 1)
    path = tmp_path / "brand.docx"
    d.save(str(path))
    c = api(server)
    doc = c.post("/api/docs", json={"template": "report", "title": "Branded"}).json()
    page.goto(f"{server}/d/{doc['id']}")
    page.click("[data-testid=tab-design]")
    with page.expect_file_chooser() as fc:
        page.click("[data-testid=match-upload]")
    fc.value.set_files(str(path))
    changes = page.locator("[data-testid=match-changes]")
    changes.wait_for(timeout=30000)
    assert "Calibri → Carlito" in changes.inner_text()
    page.wait_for_timeout(800)
    h2 = page.locator(".cd-page[data-kind=body] .cd-h2").first
    assert h2.evaluate("e => getComputedStyle(e).color") == "rgb(122, 31, 61)"
    assert "Gelasio" in h2.evaluate("e => getComputedStyle(e).fontFamily")
