"""Importing a document from the New document dialog (gh #45), in a real browser.

Skipped automatically when the built UI or Chromium is missing.
"""
from __future__ import annotations

import io

from conftest import api


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (300, 120), (20, 60, 140)).save(buf, "PNG")
    return buf.getvalue()


STUDY = b"""# Acme Workflow Study

Teams that write long documents with an agent report the same friction.

## Findings

The study covered:

- drafting time per page
- edits accepted without change

| Team | Pages |
|------|------:|
| A | 12 |
| B | 30 |

![Adoption](img/adoption.png "Figure 1. Adoption by quarter")
"""


def _docx() -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph("Acme Onboarding Proposal", style="Title")
    d.add_heading("Problem", level=1)
    d.add_paragraph("New colleagues take too long to find their feet.")
    d.add_paragraph("Welcome guide", style="List Bullet")
    d.add_picture(io.BytesIO(_png()))
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def _open_dialog(page, server):
    page.goto(server)
    page.click("[data-testid=new-doc]")
    page.wait_for_selector("[data-testid=new-doc-dialog]")


def test_import_markdown_with_its_image_from_the_dialog(page, server):
    _open_dialog(page, server)
    page.set_input_files("[data-testid=import-file]", files=[
        {"name": "study.md", "mimeType": "text/markdown", "buffer": STUDY},
        {"name": "adoption.png", "mimeType": "image/png", "buffer": _png()}])
    assert "study.md" in page.inner_text("[data-testid=import-chosen]")
    assert "+1 image" in page.inner_text("[data-testid=import-chosen]")
    page.click("[data-testid=template-report]")
    assert page.inner_text("[data-testid=create-doc]") == "Import document"
    page.click("[data-testid=create-doc]")
    # Nothing needed a look, so the document opens straight away, title on the cover.
    page.wait_for_selector(".cd-page-cover")
    assert page.locator("[data-testid=doc-tab]", has_text="Acme Workflow Study").count() == 1
    assert page.locator(".cd-page-cover .cd-h1", has_text="Acme Workflow Study").count() == 1
    page.wait_for_function("document.querySelector('.cd-page .cd-figure img')?.naturalWidth === 300")
    assert page.locator(".cd-page .cd-table tbody tr").count() == 2
    assert page.locator(".cd-page figcaption", has_text="Figure 1. Adoption by quarter").count() == 1
    did = page.url.rsplit("/", 1)[-1]
    c = api(server)
    doc = c.get(f"/api/docs/{did}").json()
    assert [b["type"] for b in doc["blocks"]] == ["heading", "text", "heading", "text", "table", "image"]
    assert doc["suggestions"] == [] and all(b["author"] != "agent" for b in doc["blocks"])
    # The imported document exports like any other.
    pdf = c.get(f"/api/docs/{did}/export", params={"format": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_import_reports_what_needs_a_look_before_opening(page, server):
    _open_dialog(page, server)
    page.set_input_files("[data-testid=import-file]", files=[
        {"name": "study.md", "mimeType": "text/markdown", "buffer": STUDY}])
    page.fill("[data-testid=new-title]", "Chosen title")
    page.click("[data-testid=create-doc]")
    page.wait_for_selector("[data-testid=import-report]")
    notes = page.locator("[data-testid=import-notes] li").all_inner_texts()
    assert notes == ["image 'img/adoption.png' is a local file and wasn't included; add it to its image block"]
    page.click("[data-testid=import-open]")
    page.wait_for_selector(".cd-page")
    assert page.locator("[data-testid=doc-tab]", has_text="Chosen title").count() == 1
    assert page.locator(".cd-page .cd-h1", has_text="Acme Workflow Study").count() == 1


def test_import_word_file_from_the_dialog(page, server):
    _open_dialog(page, server)
    page.set_input_files("[data-testid=import-file]", files=[
        {"name": "proposal.docx", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "buffer": _docx()}])
    page.click("[data-testid=create-doc]")
    page.wait_for_selector(".cd-page")
    assert page.locator("[data-testid=doc-tab]", has_text="Acme Onboarding Proposal").count() == 1
    page.wait_for_function("document.querySelector('.cd-page .cd-figure img')?.naturalWidth === 300")
    assert page.locator(".cd-page .cd-md li", has_text="Welcome guide").count() == 1


def test_import_problems_show_in_the_dialog_and_create_nothing(page, server):
    c = api(server)
    before = len(c.get("/api/docs").json())
    _open_dialog(page, server)
    page.set_input_files("[data-testid=import-file]", files=[
        {"name": "only.png", "mimeType": "image/png", "buffer": _png()}])
    page.wait_for_selector("text=Pick the Markdown or Word file too")
    assert page.locator("[data-testid=import-chosen]").count() == 0
    page.set_input_files("[data-testid=import-file]", files=[
        {"name": "empty.md", "mimeType": "text/markdown", "buffer": b"   "}])
    page.click("[data-testid=create-doc]")
    page.wait_for_selector("text=the file is empty: there is nothing to import")
    # Clearing the file goes back to a normal new document.
    page.click("[data-testid=import-clear]")
    assert page.inner_text("[data-testid=create-doc]") == "Create document"
    assert len(c.get("/api/docs").json()) == before
