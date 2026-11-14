"""Importing an existing document (gh #45): Markdown and Word files become blocks."""
from __future__ import annotations

import base64
import io
import json
import random
import zipfile

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from langstage_codraft import importers
from langstage_codraft.cli import main
from langstage_codraft.config import CodraftConfig
from langstage_codraft.importers import (
    Imported,
    folder_resolver,
    from_docx,
    from_markdown,
    import_document,
    read,
)
from langstage_codraft.server import create_app
from langstage_codraft.store import Invalid, Store, check_block


def png(color=(20, 60, 140), size=(60, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def types(imp: Imported) -> list[str]:
    return [b["type"] for b in imp.blocks]


def md_of(imp: Imported) -> list[str]:
    return [b["content"].get("markdown", b["content"].get("text", "")) for b in imp.blocks]


SAMPLE = """\
# Acme Field Report

Intro with **bold** text.

## Findings

The steps are:

- one
- two
    - nested

> A plain quote
> continues.

> [!WARNING]
> Mind the gap.

| Name | Value |
|------|------:|
| a \\| b | **1** |
| c | `2` | extra |
| short |

```python
# not a heading
- not a list
```

***

Closing words.
"""


# ---------------------------------------------------------------------------- Markdown
def test_markdown_structure_maps_to_block_types():
    imp = from_markdown(SAMPLE)
    assert imp.title == "Acme Field Report"
    assert types(imp) == ["heading", "text", "heading", "text", "text", "callout", "table", "text",
                          "divider", "text"]
    assert imp.blocks[0]["content"] == {"text": "Acme Field Report", "level": 1}
    assert imp.blocks[2]["content"]["level"] == 2
    # A list introduced by "...:" stays with its sentence; nesting survives.
    assert imp.blocks[3]["content"]["markdown"] == "The steps are:\n\n- one\n- two\n    - nested"
    assert imp.blocks[4]["content"]["markdown"] == "> A plain quote\n> continues."
    assert imp.blocks[5]["content"] == {"markdown": "**Warning:** Mind the gap.", "tone": "warn"}
    # Cells are plain text; an escaped pipe is a pipe; ragged rows are squared off.
    assert imp.blocks[6]["content"]["rows"] == [["Name", "Value"], ["a | b", "1"], ["c", "2"], ["short", ""]]
    # Code keeps its fence, and what's inside is not read as structure.
    assert imp.blocks[7]["content"]["markdown"] == "```python\n# not a heading\n- not a list\n```"
    assert imp.notes == []


def test_every_mapped_block_is_one_the_store_accepts():
    for split in importers.SPLITS:
        for b in from_markdown(SAMPLE, split=split).blocks:
            check_block(b["type"], b["content"], b.get("layout"))


def test_split_by_section_keeps_prose_between_headings_together():
    imp = from_markdown(SAMPLE, split="section")
    assert types(imp) == ["heading", "text", "heading", "text", "callout", "table", "text", "divider", "text"]
    assert imp.blocks[3]["content"]["markdown"].count("\n\n") == 2  # sentence+list, then the quote
    with pytest.raises(Invalid, match="split must be one of"):
        from_markdown(SAMPLE, split="page")


@pytest.mark.parametrize("text,title,first", [
    ("---\ntitle: \"Front Title\"\nauthor: Jordan Example\ndate: 2026-01-05\n---\n\nBody.", "Front Title", "heading"),
    ("---\ntitle: Front Title\n---\n\n# Heading Title\n\nBody.", "Front Title", "heading"),
    ("Title By Underline\n==================\n\nBody.", "Title By Underline", "heading"),
    ("Body only, no title.", "", "text"),
    ("## Starts at level two\n\nBody.", "", "heading"),
])
def test_title_comes_from_front_matter_or_a_leading_h1(text, title, first):
    imp = from_markdown(text)
    assert imp.title == title
    assert imp.blocks[0]["type"] == first


def test_front_matter_title_gets_a_heading_and_a_byline():
    imp = from_markdown("---\ntitle: Front Title\nauthor: Jordan Example\ndate: 2026-01-05\n---\n\nBody.")
    assert [b.get("meta") for b in imp.blocks] == ["title", "subtitle", None]
    assert imp.blocks[1]["content"]["markdown"] == "*Jordan Example · 2026-01-05*"
    # One H1 only: the file's own heading is not duplicated.
    two = from_markdown("---\ntitle: Front Title\n---\n\n# Heading Title\n")
    assert types(two) == ["heading"] and two.blocks[0]["content"]["text"] == "Heading Title"


def test_a_rule_at_the_top_is_not_front_matter():
    imp = from_markdown("---\n\nJust a paragraph after a rule.\n\n---\n\nAnother.")
    assert types(imp) == ["divider", "text", "divider", "text"]


def test_setext_heading_versus_rule():
    imp = from_markdown("Section\n-------\n\ntext\n\n---\n\nmore")
    assert types(imp) == ["heading", "text", "divider", "text"]
    assert imp.blocks[0]["content"] == {"text": "Section", "level": 2}


def test_reference_links_are_inlined_but_code_is_left_alone():
    imp = from_markdown("See [the guide][g], [g], and `[g]`.\n\n```\n[the guide][g]\n```\n\n[g]: https://example.org/guide \"Guide\"")
    assert imp.blocks[0]["content"]["markdown"] == (
        'See [the guide](https://example.org/guide "Guide"), [g](https://example.org/guide "Guide"), and `[g]`.')
    assert "[the guide][g]" in imp.blocks[1]["content"]["markdown"]
    assert len(imp.blocks) == 2  # the definition is not a paragraph


def test_footnotes_become_block_references_cited_by_key():
    imp = from_markdown(
        "First claim.[^b]\n\nSecond claim.[^a] Again.[^b]\n\n"
        "[^a]: Example Institute, *Review Economics* (2025).\n"
        "[^b]: Acme Handbook, https://example.org/handbook.\n[^c]: never used\n")
    # A marker becomes a citation ([@key]), which prints as the source's number in the list
    # and keeps pointing at it when blocks move (0.9.0; it used to be a typed-in "[1]").
    assert imp.blocks[0]["content"]["markdown"] == "First claim.[@acmehandbook]"
    assert imp.blocks[0]["refs"] == [{"title": "Acme Handbook", "url": "https://example.org/handbook",
                                      "key": "acmehandbook"}]
    assert imp.blocks[1]["content"]["markdown"] == "Second claim.[@exampleinstitute] Again.[@acmehandbook]"
    assert [r["title"] for r in imp.blocks[1]["refs"]] == ["Example Institute, Review Economics (2025).", "Acme Handbook"]
    assert types(imp)[-1] == "references"
    assert any("[^c]" in n for n in imp.notes) and any("footnotes became references" in n for n in imp.notes)


def test_an_undefined_footnote_stays_as_text_and_is_reported():
    imp = from_markdown("Claim.[^nope]")
    assert imp.blocks[0]["content"]["markdown"] == "Claim.[^nope]"
    assert "references" not in types(imp)
    assert any("[^nope] has no definition" in n for n in imp.notes)


def test_unknown_constructs_degrade_to_a_paragraph_and_are_reported():
    imp = from_markdown("<details><summary>More</summary>\nHidden <b>text</b>\n</details>\n\n<!-- a comment -->\n\nTerm\n: definition\n\n$$x^2$$")
    assert types(imp) == ["text", "text", "text"]
    assert imp.blocks[0]["content"]["markdown"] == "More\n\nHidden text"
    assert imp.notes == ["an HTML block was imported as plain text"]


def test_html_blocks_keep_their_pictures_and_text():
    imp = from_markdown(
        '<p align="center">\n  <a href="https://example.org/v.mp4"><img src="https://example.org/play.jpg" alt="Play"></a>\n'
        "  <br><sub>Watch the video</sub>\n</p>\n\n<details><summary>Dark mode</summary>\n\n"
        "![Dark](https://example.org/dark.png)\n\n</details>\n")
    assert types(imp) == ["image", "text", "text", "image"]
    assert imp.blocks[0]["content"] == {"src": "https://example.org/play.jpg", "alt": "Play", "caption": ""}
    assert md_of(imp)[1:3] == ["Watch the video", "Dark mode"]
    assert imp.notes == ["an HTML block was imported as plain text"]


def test_a_row_of_badges_stays_one_line_of_text():
    row = ("[![Tests](https://example.org/t.svg)](https://example.org/t)\n"
           "[![PyPI](https://example.org/p.svg)](https://example.org/p)")
    imp = from_markdown(f"# T\n\n{row}\n\n![One](https://example.org/one.png)")
    assert types(imp) == ["heading", "text", "image"]
    assert imp.blocks[1]["content"]["markdown"] == row


def test_page_breaks():
    imp = from_markdown("One\n\n\\newpage\n\nTwo\n\n<div style=\"page-break-after: always\"></div>\n\nThree")
    assert types(imp) == ["text", "page-break", "text", "page-break", "text"]


def test_images_embedded_remote_and_local():
    data_uri = "data:image/png;base64," + base64.b64encode(png()).decode()
    seen: list[str] = []

    def resolve(path):
        seen.append(path)
        return ("chart.png", png((200, 80, 40))) if path == "img/chart.png" else None

    imp = from_markdown(
        f'![Embedded]({data_uri})\n\n![Remote](https://example.org/x.png "Figure 2")\n\n'
        "![Chart](img/chart.png)\n\n![Gone](missing.png)\n\n"
        '<p align="center"><img src="img/chart.png" alt="Logo"></p>\n', resolve=resolve)
    assert types(imp) == ["image"] * 5
    srcs = [b["content"]["src"] for b in imp.blocks]
    # A remote image stays a URL (nothing is fetched); the same file is stored once.
    assert srcs == ["asset:0", "https://example.org/x.png", "asset:1", "", "asset:1"]
    assert imp.blocks[1]["content"]["caption"] == "Figure 2" and imp.blocks[4]["content"]["alt"] == "Logo"
    assert [a.mime for a in imp.assets] == ["image/png", "image/png"]
    assert seen == ["img/chart.png", "missing.png", "img/chart.png"]
    assert imp.notes == ["image 'missing.png' was not found; add it to its image block"]


def test_a_local_image_without_a_resolver_leaves_a_placeholder():
    imp = from_markdown("![Chart](img/chart.png)")
    assert imp.blocks[0]["content"] == {"src": "", "alt": "Chart", "caption": ""}
    assert imp.notes == ["image 'img/chart.png' is a local file and wasn't included; add it to its image block"]


def test_an_image_inside_a_sentence_follows_its_paragraph():
    data_uri = "data:image/png;base64," + base64.b64encode(png()).decode()
    imp = from_markdown(f"Before ![icon]({data_uri}) after, and ![badge](https://example.org/b.svg) stays.")
    assert types(imp) == ["text", "image"]
    assert imp.blocks[0]["content"]["markdown"] == "Before after, and ![badge](https://example.org/b.svg) stays."


@pytest.mark.parametrize("src,why", [
    ("data:text/html;base64,PGI+", "isn't base64 image data"),
    ("data:image/png;base64,@@@", "invalid base64"),
    ("data:image/png,rawbytes", "isn't base64 image data"),
    ("file:///etc/passwd", "unsupported address"),
    ("javascript:alert(1)", "unsupported address"),
    ("vbscript:x", "unsupported address"),
])
def test_bad_image_sources_are_left_out_with_a_reason(src, why):
    imp = from_markdown(f"![x]({src})")
    assert imp.blocks[0] == {"type": "image", "content": {"src": "", "alt": "x", "caption": ""}} and not imp.assets
    assert why in imp.notes[0]


def test_image_url_with_parentheses():
    imp = from_markdown("![x](https://example.org/a_(b).png)")
    assert imp.blocks[0]["content"]["src"] == "https://example.org/a_(b).png"


def test_image_bytes_are_judged_by_content_not_name():
    def resolve(path):
        return (path, {"fake.png": b"MZ\x90 not an image", "cut.png": png()[:40],
                       "evil.svg": b'<svg viewBox="0 0 1 1"><script>alert(1)</script><rect onload="x()" width="1" height="1"/></svg>'}[path])

    imp = from_markdown("![a](fake.png)\n\n![b](cut.png)\n\n![c](evil.svg)", resolve=resolve)
    assert [b["content"]["src"] for b in imp.blocks] == ["", "", "asset:0"]
    assert "not a PNG, JPEG" in imp.notes[0] and "damaged" in imp.notes[1]
    svg = imp.assets[0].data.decode()
    assert imp.assets[0].mime == "image/svg+xml" and "script" not in svg and "onload" not in svg


def test_folder_resolver_reads_only_inside_the_folder(tmp_path):
    (tmp_path / "docs" / "img").mkdir(parents=True)
    (tmp_path / "docs" / "img" / "ok.png").write_bytes(png())
    (tmp_path / "secret.png").write_bytes(png())
    resolve = folder_resolver(tmp_path / "docs")
    assert resolve("img/ok.png")[0] == "ok.png"
    assert resolve("img\\ok.png")[0] == "ok.png"
    assert resolve("img/none.png") is None
    for bad in ("../secret.png", "img/../../secret.png", str(tmp_path / "secret.png"), "/etc/passwd", "C:\\Windows\\win.ini"):
        with pytest.raises(ValueError, match="only images in the imported file's folder"):
            resolve(bad)
    imp = from_markdown("![s](../secret.png)", resolve=resolve)
    assert imp.blocks[0]["content"]["src"] == "" and not imp.assets and "outside it" in imp.notes[0]


def test_markdown_never_raises_on_junk():
    rnd = random.Random(7)
    pieces = ["#", "##", "-", "1.", ">", "|", "---", "===", "```", "~~~", "[", "]", "(", ")", "![", "[^", "]:",
              "<div>", "</div>", "<!--", "-->", "\t", "    ", "*", "_", "`", "\\", "word", "", "\n", "\n\n", ":", "$$",
              "<img src=x>", "[!NOTE]", "\ufeff", "\x0b", "é", "|---|"]
    for _ in range(300):
        text = " ".join(rnd.choice(pieces) for _ in range(rnd.randint(1, 60))).replace(" \n ", "\n")
        for split in importers.SPLITS:
            imp = from_markdown(text, split=split)
            for b in imp.blocks:
                check_block(b["type"], b["content"], b.get("layout"))
                if b["type"] == "table":
                    assert all(isinstance(c, str) for r in b["content"]["rows"] for c in r)


def test_code_is_found_in_one_pass_and_left_alone():
    text = "a `x [g]` b ``y ` [g]`` c ` lone [g]\n```\n[g] fenced\n```\nafter [g] `open"
    got = [text[s:e] for s, e in importers._code_ranges(text)]
    assert got == ["`x [g]`", "``y ` [g]``", "```\n[g] fenced\n```"]
    imp = from_markdown(text + "\n\n[g]: https://example.org/g")
    assert types(imp) == ["text", "text", "text"]
    md = "|".join(md_of(imp))
    assert md.count("(https://example.org/g)") == 2  # the lone one and the one after the fence
    assert "`x [g]`" in md and "``y ` [g]``" in md and "[g] fenced" in md
    assert importers._drop_elements("a<script>x</script>b<STYLE>y</style >c<script>never closed") == "abc"


def test_input_built_to_backtrack_is_handled_in_linear_time():
    import time

    start = time.monotonic()
    for text in ("<script>" * 150_000, "".join("`" * k + " a " for k in range(1, 46)) * 1500,
                 "<div " * 300_000, "![a " * 300_000, "# " + "**a " * 300_000, "<http://a" * 150_000):
        assert len(from_markdown(text).blocks) <= 1
    assert time.monotonic() - start < 30  # these took minutes before the scans were made linear


def test_markdown_length_limits(monkeypatch):
    monkeypatch.setattr(importers, "MAX_MARKDOWN_LINES", 50)
    with pytest.raises(Invalid, match=r"too long to import \(101 lines; the limit is 50\)"):
        from_markdown("a\n" * 100)
    monkeypatch.setattr(importers, "MAX_MARKDOWN_LINES", 10**6)
    monkeypatch.setattr(importers, "MAX_ITEMS", 20)
    with pytest.raises(Invalid, match="more than 20 paragraphs"):
        from_markdown("a\n\n" * 100)


# ----------------------------------------------------------------------- read(): refusals
@pytest.mark.parametrize("name,data,why", [
    ("a.md", b"", "the file is empty"),
    ("a.md", b"  \n\t\n", "the file is empty"),
    ("a.md", b"<!-- only a comment -->\n\n[x]: https://example.org\n", "nothing to import"),
    ("a.md", b"PK\x03\x04\x00\x00\x00\x00binary", "doesn't look like text"),
    ("a.pdf", b"%PDF-1.7", "can't import a .pdf file"),
    ("a.doc", b"\xd0\xcf\x11\xe0", "save it as .docx"),
    ("noext", b"hello", "file without an extension"),
    ("a.docx", b"not a zip at all", "not a Word .docx file"),
    ("a.docx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64, "legacy Word .doc"),
])
def test_files_that_cannot_be_imported_are_refused_with_a_reason(name, data, why):
    with pytest.raises(Invalid, match=why):
        read(name, data)


def test_size_and_block_limits(monkeypatch):
    monkeypatch.setattr(importers, "MAX_IMPORT_BYTES", 1000)
    with pytest.raises(Invalid, match="too large to import"):
        read("big.md", b"word " * 400)
    monkeypatch.setattr(importers, "MAX_IMPORT_BYTES", 10**7)
    monkeypatch.setattr(importers, "MAX_BLOCKS", 50)
    many = "\n\n".join(f"Paragraph {i}." for i in range(80)).encode()
    with pytest.raises(Invalid, match=r"maps to 80 blocks; the limit is 50\. Try split=section"):
        read("many.md", many)
    assert len(read("many.md", many, split="section").blocks) == 1


def test_text_that_is_not_utf8_is_read_and_reported():
    imp = read("latin.md", "Café résumé".encode("cp1252"))
    assert imp.blocks[0]["content"]["markdown"] == "Café résumé"
    assert imp.notes == ["the file is not valid UTF-8; it was read as Windows-1252"]
    assert read("bom.md", "\ufeff# Títle".encode()).title == "Títle"
    assert read("wide.md", "# Wide".encode("utf-16")).title == "Wide"


# ---------------------------------------------------------------------------------- Word
def make_docx(build) -> bytes:
    import docx

    d = docx.Document()
    build(d)
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def rezip(data: bytes, edit) -> bytes:
    """Rewrite a .docx's parts: ``edit`` maps {name: bytes} in place."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        parts = {i.filename: z.read(i.filename) for i in z.infolist()}
    edit(parts)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, blob in parts.items():
            z.writestr(name, blob)
    return out.getvalue()


def _sample_docx() -> bytes:
    from docx.enum.text import WD_BREAK
    from docx.shared import Inches

    def build(d):
        d.core_properties.title = "Core title"
        d.add_paragraph("Acme Annual Review", style="Title")
        d.add_paragraph("Prepared for the board", style="Subtitle")
        d.add_heading("Summary", level=1)
        p = d.add_paragraph("Revenue grew ")
        p.add_run("12 percent").bold = True
        p.add_run(" see ")
        p.add_run("the_plan").italic = True
        p.add_run(". Use ")
        p.add_run("pip install acme").font.name = "Consolas"
        p.add_run(" and snake_case * star [x].")
        d.add_paragraph("The priorities are:")
        d.add_paragraph("Grow", style="List Bullet")
        d.add_paragraph("Nested", style="List Bullet 2")
        d.add_paragraph("First", style="List Number")
        d.add_paragraph("A wise quote.", style="Quote")
        t = d.add_table(rows=2, cols=2)
        for cell, v in zip([c for r in t.rows for c in r.cells], ["Region", "Q1", "North", "10"], strict=True):
            cell.text = v
        d.add_paragraph("Table 1. Sales", style="Caption")
        d.add_picture(io.BytesIO(png()), width=Inches(2))
        d.add_paragraph("Figure 1. A box", style="Caption")
        d.add_heading("Details", level=2)
        d.add_paragraph("Before break.").add_run().add_break(WD_BREAK.PAGE)
        d.add_paragraph("# not a heading")
        d.add_paragraph("")
        d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)  # a trailing page break is dropped

    return make_docx(build)


def test_docx_structure_maps_to_block_types():
    imp = from_docx(_sample_docx())
    assert imp.title == "Acme Annual Review"
    assert types(imp) == ["heading", "text", "heading", "text", "text", "text", "table", "image", "heading",
                          "text", "page-break", "text"]
    assert [b.get("meta") for b in imp.blocks[:3]] == ["title", "subtitle", None]
    # Word's Title is the H1, so Heading 1 and 2 move down a level.
    assert [b["content"]["level"] for b in imp.blocks if b["type"] == "heading"] == [1, 2, 3]
    assert imp.blocks[1]["content"]["markdown"] == "*Prepared for the board*"
    assert imp.blocks[3]["content"]["markdown"] == (
        "Revenue grew **12 percent** see *the_plan*. Use `pip install acme` and snake_case \\* star \\[x\\].")
    assert imp.blocks[4]["content"]["markdown"] == "The priorities are:\n\n- Grow\n    - Nested\n1. First"
    assert imp.blocks[5]["content"]["markdown"] == "> A wise quote."
    assert imp.blocks[6]["content"] == {"rows": [["Region", "Q1"], ["North", "10"]], "header": True,
                                        "caption": "Table 1. Sales"}
    assert imp.blocks[7]["content"] == {"src": "asset:0", "alt": "", "caption": "Figure 1. A box"}
    assert imp.blocks[11]["content"]["markdown"] == "\\# not a heading"
    assert [a.mime for a in imp.assets] == ["image/png"] and imp.notes == []
    for b in imp.blocks:
        check_block(b["type"], b["content"], b.get("layout"))


def test_docx_without_a_title_style_keeps_heading_levels_and_uses_the_core_title():
    def build(d):
        d.core_properties.title = "Core title"
        d.add_heading("One", level=1)
        d.add_paragraph("Body.")

    imp = from_docx(make_docx(build))
    assert imp.title == "Core title" and imp.blocks[0]["content"] == {"text": "One", "level": 1}


def test_docx_headings_that_already_start_below_the_title_keep_their_levels():
    # Codraft's own Word export writes a cover title as Title and sections as Heading 2.
    def build(d):
        d.add_paragraph("Acme Study", style="Title")
        d.add_heading("Background", level=2)
        d.add_heading("Detail", level=3)

    imp = from_docx(make_docx(build))
    assert [b["content"]["level"] for b in imp.blocks] == [1, 2, 3]


W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R_NS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def test_docx_links_footnotes_and_tracked_changes():
    base = make_docx(lambda d: d.add_paragraph("PLACEHOLDER"))

    def edit(parts):
        body = (
            '<w:p><w:r><w:t xml:space="preserve">See </w:t></w:r>'
            '<w:hyperlink r:id="rIdLink"><w:r><w:t>the guide</w:t></w:r></w:hyperlink>'
            '<w:r><w:t>.</w:t></w:r><w:r><w:footnoteReference w:id="2"/></w:r>'
            '<w:ins w:id="1" w:author="a"><w:r><w:t xml:space="preserve"> Added.</w:t></w:r></w:ins>'
            '<w:del w:id="2" w:author="a"><w:r><w:delText> Removed.</w:delText></w:r></w:del></w:p>')
        doc = parts["word/document.xml"].decode()
        start, end = doc.index("<w:p>"), doc.index("</w:p>") + len("</w:p>")
        parts["word/document.xml"] = (doc[:start] + body + doc[end:]).encode()
        rels = parts["word/_rels/document.xml.rels"].decode()
        parts["word/_rels/document.xml.rels"] = rels.replace("</Relationships>", (
            '<Relationship Id="rIdLink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"'
            ' Target="https://example.org/guide" TargetMode="External"/>'
            '<Relationship Id="rIdFn" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"'
            ' Target="footnotes.xml"/></Relationships>')).encode()
        parts["word/footnotes.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:footnotes {W_NS} {R_NS}>'
            '<w:footnote w:type="separator" w:id="0"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
            '<w:footnote w:id="2"><w:p><w:r><w:t>Acme Handbook, https://example.org/handbook</w:t></w:r></w:p></w:footnote>'
            '</w:footnotes>').encode()
        types_xml = parts["[Content_Types].xml"].decode()
        parts["[Content_Types].xml"] = types_xml.replace("</Types>", (
            '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.footnotes+xml"/></Types>')).encode()

    imp = from_docx(rezip(base, edit))
    assert types(imp) == ["text", "references"]
    assert imp.blocks[0]["content"]["markdown"] == "See [the guide](https://example.org/guide).[@acmehandbook] Added."
    assert imp.blocks[0]["refs"] == [{"title": "Acme Handbook", "url": "https://example.org/handbook",
                                      "key": "acmehandbook"}]
    assert "tracked changes were accepted (deleted text was left out)" in imp.notes


def test_docx_one_cell_table_is_a_box_around_text_and_merged_cells_stay_square():
    def build(d):
        box = d.add_table(rows=1, cols=1)
        box.cell(0, 0).text = "Boxed text"
        t = d.add_table(rows=2, cols=3)
        t.cell(0, 0).merge(t.cell(0, 1)).text = "Wide"
        t.cell(0, 2).text = "C"
        for i, v in enumerate(["a", "b", "c"]):
            t.cell(1, i).text = v

    imp = from_docx(make_docx(build))
    assert types(imp) == ["text", "table"]
    assert imp.blocks[0]["content"]["markdown"] == "Boxed text"
    assert imp.blocks[1]["content"]["rows"] == [["Wide", "", "C"], ["a", "b", "c"]]


def test_docx_with_malformed_values_is_read_or_refused_never_a_traceback():
    def edit(parts):
        doc = parts["word/document.xml"].decode()
        doc = doc.replace("<w:p>", '<w:p><w:pPr><w:numPr><w:ilvl w:val="x"/><w:numId w:val="7"/></w:numPr>'
                          '<w:outlineLvl w:val="zz"/></w:pPr>', 1)
        parts["word/document.xml"] = doc.encode()

    imp = from_docx(rezip(make_docx(lambda d: d.add_paragraph("Item")), edit))
    assert types(imp) == ["text"] and imp.blocks[0]["content"]["markdown"].endswith(" Item")

    def boom(*a, **k):
        raise RuntimeError("unexpected")

    import unittest.mock as mock

    with mock.patch.object(importers, "_md_escape", boom), pytest.raises(Invalid, match="could not read the Word file"):
        from_docx(make_docx(lambda d: d.add_paragraph("Item")))
    with mock.patch.object(importers, "_scan", boom), pytest.raises(Invalid, match="could not import the file"):
        read("a.md", b"# T")


def test_docx_zip_guards(monkeypatch):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(Invalid, match="no word/document.xml"):
        from_docx(out.getvalue())
    monkeypatch.setattr(importers, "MAX_DOCX_UNZIPPED", 2000)
    with pytest.raises(Invalid, match="too large to import"):
        from_docx(_sample_docx())
    monkeypatch.setattr(importers, "MAX_DOCX_UNZIPPED", 10**9)
    monkeypatch.setattr(importers, "MAX_DOCX_XML", 500)
    with pytest.raises(Invalid, match="MB of text"):
        from_docx(_sample_docx())
    monkeypatch.setattr(importers, "MAX_DOCX_XML", 10**9)
    broken =rezip(_sample_docx(), lambda parts: parts.update({"word/document.xml": b"<not-xml"}))
    with pytest.raises(Invalid, match="could not read the Word file"):
        from_docx(broken)


# ------------------------------------------------------------- creating the document
@pytest.fixture
def cfg(tmp_path):
    return CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data"}, use_toml=False)


def test_import_document_writes_author_blocks_and_stores_images(store, cfg):
    data_uri = "data:image/png;base64," + base64.b64encode(png()).decode()
    doc, imp = import_document(store, cfg, {}, "field_report.md",
                               f"# Acme Field Report\n\nBody.\n\n![Chart]({data_uri})\n".encode(), author="Jordan")
    full = store.full(doc["id"])
    assert full["title"] == "Acme Field Report" and [b["type"] for b in full["blocks"]] == ["heading", "text", "image"]
    # The author's own content: versions by the author, and nothing waiting for approval.
    assert {b["author"] for b in full["blocks"]} == {"Jordan"} and full["suggestions"] == []
    assert store.versions(full["blocks"][0]["id"])["versions"][0]["note"] == "imported from field_report.md"
    up = full["uploads"][0]
    assert full["blocks"][2]["content"]["src"] == up["id"] and up["purpose"] == "asset" and up["mime"] == "image/png"
    assert imp.summary() == {"blocks": 3, "images": 1, "notes": []}


@pytest.mark.parametrize("title,name,expect", [
    ("Chosen", "x.md", "Chosen"), (None, "x.md", "From File"), ("  ", "x.md", "From File")])
def test_title_precedence(store, cfg, title, name, expect):
    doc, _ = import_document(store, cfg, {}, name, b"# From File\n\nBody.", title=title)
    assert doc["title"] == expect
    # The heading keeps the file's words either way.
    assert store.list_blocks(doc["id"])[0]["content"]["text"] == "From File"


def test_title_falls_back_to_the_file_name(store, cfg):
    doc, _ = import_document(store, cfg, {}, "quarterly_notes.md", b"Body only.")
    assert doc["title"] == "quarterly notes"


def test_template_supplies_the_look_not_its_placeholder_text(store, cfg):
    src = b"---\ntitle: Acme Study\nauthor: Jordan Example\n---\n\n## Background\n\nBody."
    doc, _ = import_document(store, cfg, {}, "s.md", src, template="report")
    blocks = store.list_blocks(doc["id"])
    assert [b["content"].get("text") or b["content"].get("markdown") for b in blocks] == [
        "Acme Study", "*Jordan Example*", "Background", "Body."]
    assert [b["layout"]["role"] for b in blocks] == ["cover", "cover", "flow", "flow"]
    doc, _ = import_document(store, cfg, {}, "s.md", src, template="paper", theme="modern")
    blocks = store.list_blocks(doc["id"])
    assert doc["settings"]["columns"] == 2 and doc["settings"]["theme"] == "modern"
    assert [b["layout"]["span"] for b in blocks] == ["full", "full", "column", "column"]
    doc, _ = import_document(store, cfg, {}, "s.md", src, settings={"page_size": "a4"})
    assert doc["settings"]["page_size"] == "a4"
    assert {b["layout"]["span"] for b in store.list_blocks(doc["id"])} == {"column"}


def test_house_defaults_apply_like_a_new_document(store, cfg):
    doc, _ = import_document(store, cfg, {"margins": {"top": 30}, "columns": 2}, "s.md", b"# T\n\nBody.")
    assert doc["settings"]["margins"]["top"] == 30 and doc["settings"]["columns"] == 2


@pytest.mark.parametrize("kw,why", [
    ({"template": "nope"}, "unknown template 'nope'"),
    ({"theme": "nope"}, "unknown theme 'nope'"),
    ({"settings": {"columns": 9}}, "columns"),
    ({"settings": ["x"]}, "settings must be an object"),
    ({"split": "page"}, "split must be one of"),
])
def test_a_refused_import_creates_nothing(store, cfg, kw, why):
    with pytest.raises((Invalid, ValueError), match=why):
        import_document(store, cfg, {}, "s.md", b"# T\n\nBody.", **kw)
    assert store.list_documents() == []


def test_a_failed_import_leaves_no_document_or_files(store, cfg, monkeypatch):
    data_uri = "data:image/png;base64," + base64.b64encode(png()).decode()

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "insert_blocks", boom)
    with pytest.raises(RuntimeError):
        import_document(store, cfg, {}, "s.md", f"# T\n\n![c]({data_uri})".encode())
    assert store.list_documents() == [] and list(store.uploads_dir.iterdir()) == []


def test_insert_blocks_is_all_or_nothing(store):
    doc = store.create_document("d")
    store.insert_block(doc["id"], "text", {"markdown": "existing"})
    with pytest.raises(Invalid, match="unknown block type"):
        store.insert_blocks(doc["id"], [{"type": "text", "content": {"markdown": "a"}}, {"type": "nope"}])
    assert [b["content"]["markdown"] for b in store.list_blocks(doc["id"])] == ["existing"]
    rev = store.rev(doc["id"])
    ids = store.insert_blocks(doc["id"], [{"type": "text", "content": {"markdown": "a"}}, {"type": "divider"}],
                              author="Jordan", note="imported")
    blocks = store.list_blocks(doc["id"])
    assert [b["id"] for b in blocks[1:]] == ids and blocks[1]["author"] == "Jordan"
    assert store.rev(doc["id"]) == rev + 1


# ---------------------------------------------------------------------------------- REST
def _app(tmp_path, **over):
    conf = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799, **over}, use_toml=False)
    return create_app(conf, None, {"kind": "none"}, store=Store(tmp_path / "data" / "codraft.db"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with TestClient(_app(tmp_path)) as c:
        yield c


def test_rest_import_markdown_with_its_images(client):
    r = client.post("/api/docs/import", data={"template": "report", "author": "Jordan"}, files=[
        ("file", ("study.md", b"# Acme Study\n\nBody.\n\n![Chart](img/chart.png)\n\n![Gone](gone.png)\n", "text/markdown")),
        ("assets", ("chart.png", png(), "image/png"))])
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["title"] == "Acme Study" and [b["type"] for b in doc["blocks"]] == ["heading", "text", "image", "image"]
    assert doc["blocks"][0]["layout"]["role"] == "cover" and doc["blocks"][0]["author"] == "Jordan"
    assert doc["import"] == {"blocks": 4, "images": 1,
                             "notes": ["image 'gone.png' was not found; add it to its image block"]}
    served = client.get(f"/api/uploads/{doc['blocks'][2]['content']['src']}/file")
    assert served.status_code == 200 and served.headers["content-type"] == "image/png" and served.content == png()
    assert len(client.get("/api/docs").json()) == 1


def test_rest_import_word_and_settings(client):
    r = client.post("/api/docs/import", data={"title": "Chosen", "settings": json.dumps({"page_size": "a4", "columns": 2})},
                    files={"file": ("review.docx", _sample_docx(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["title"] == "Chosen" and doc["settings"]["page_size"] == "a4"
    assert doc["blocks"][0]["layout"]["span"] == "full" and doc["import"]["images"] == 1
    assert client.get(f"/api/uploads/{doc['uploads'][0]['id']}/file").headers["content-type"] == "image/png"


@pytest.mark.parametrize("data,file,status,why", [
    ({}, ("a.md", b""), 400, "the file is empty"),
    ({}, ("a.pdf", b"%PDF"), 400, "can't import a .pdf file"),
    ({}, ("a.docx", b"junk"), 400, "not a Word .docx file"),
    ({"template": "nope"}, ("a.md", b"# T"), 400, "unknown template"),
    ({"theme": "nope"}, ("a.md", b"# T"), 400, "unknown theme"),
    ({"settings": "{not json"}, ("a.md", b"# T"), 400, "settings must be a JSON object"),
    ({"settings": "[1]"}, ("a.md", b"# T"), 400, "settings must be an object"),
    ({"split": "page"}, ("a.md", b"# T"), 400, "split must be one of"),
])
def test_rest_import_refusals(client, data, file, status, why):
    r = client.post("/api/docs/import", data=data, files={"file": file})
    assert r.status_code == status and why in r.json()["detail"], r.text
    assert client.get("/api/docs").json() == []


def test_rest_import_needs_a_file_and_caps_its_size(client, monkeypatch):
    assert client.post("/api/docs/import", data={"title": "x"}).status_code == 422
    monkeypatch.setattr(importers, "MAX_IMPORT_BYTES", 100)
    r = client.post("/api/docs/import", files={"file": ("a.md", b"word " * 100)})
    assert r.status_code == 413 and "file too large" in r.json()["detail"]
    r = client.post("/api/docs/import", files=[("file", ("a.md", b"# T\n\n![c](c.png)")),
                                               ("assets", ("c.png", b"x" * 200, "image/png"))])
    assert r.status_code == 413
    assert client.get("/api/docs").json() == []


def test_rest_import_is_behind_the_same_guards_as_other_writes(tmp_path):
    files = {"file": ("a.md", b"# T")}
    with TestClient(_app(tmp_path / "a")) as c:
        for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            r = c.post("/api/docs/import", headers=headers, files=files)
            assert r.status_code == 403 and "Cross-site request refused" in r.json()["detail"]
        assert c.get("/api/docs").json() == []
    with TestClient(_app(tmp_path / "b", auth_password="pw"), client=("127.0.0.1", 5000)) as c:
        assert c.post("/api/docs/import", files=files).status_code == 401
        tok = base64.b64encode(b"admin:pw").decode()
        assert c.post("/api/docs/import", files=files, headers={"Authorization": f"Basic {tok}"}).status_code == 200


def test_rest_import_uses_the_signed_in_user(tmp_path):
    with TestClient(_app(tmp_path, user_header="X-User")) as c:
        doc = c.post("/api/docs/import", data={"author": "Spoofed"}, headers={"X-User": "jordan@example.org"},
                     files={"file": ("a.md", b"# T")}).json()
        assert doc["blocks"][0]["author"] not in ("Spoofed", "user", "agent")


# ----------------------------------------------------------------------------------- CLI
def test_cli_import_markdown_reads_images_next_to_the_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs" / "img").mkdir(parents=True)
    (tmp_path / "docs" / "img" / "chart.png").write_bytes(png())
    (tmp_path / "secret.png").write_bytes(png())
    (tmp_path / "docs" / "study.md").write_text(
        "# Acme Study\n\nBody.\n\n![Chart](img/chart.png)\n\n![Secret](../secret.png)\n", encoding="utf-8")
    res = CliRunner().invoke(main, ["import", "docs/study.md", "--data-dir", "data", "--template", "paper"])
    assert res.exit_code == 0, res.output
    doc_id = res.stdout.strip().splitlines()[-1]
    assert doc_id.startswith("doc_")
    assert "[note] image '../secret.png' was left out: only images in the imported file's folder" in res.stderr
    store = Store(tmp_path / "data" / "codraft.db")
    blocks = store.list_blocks(doc_id)
    assert [b["type"] for b in blocks] == ["heading", "text", "image", "image"]
    assert blocks[2]["content"]["src"].startswith("upl_") and blocks[3]["content"]["src"] == ""
    assert len(store.list_uploads(doc_id)) == 1 and store.get_document(doc_id)["settings"]["columns"] == 2
    # `list` and `new` see the same store.
    assert "Acme Study" in CliRunner().invoke(main, ["list", "--data-dir", "data"]).output


def test_cli_import_word_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "review.docx").write_bytes(_sample_docx())
    res = CliRunner().invoke(main, ["import", "review.docx", "--data-dir", "data", "--title", "Chosen", "--json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out["title"] == "Chosen" and out["blocks"] == 12 and out["images"] == 1 and out["notes"] == []


@pytest.mark.parametrize("name,data,args,why", [
    ("missing.md", None, [], "can't read missing.md"),
    ("empty.md", b"", [], "can't import empty.md: the file is empty"),
    ("a.pdf", b"%PDF", [], "can't import a.pdf: can't import a .pdf file"),
    ("bad.docx", b"junk", [], "can't import bad.docx: not a Word .docx file"),
    ("a.md", b"# T", ["--template", "nope"], "unknown template 'nope'"),
    ("a.md", b"# T", ["--theme", "nope"], "unknown theme 'nope'"),
])
def test_cli_import_failures_are_one_line_not_a_traceback(tmp_path, monkeypatch, name, data, args, why):
    monkeypatch.chdir(tmp_path)
    if data is not None:
        (tmp_path / name).write_bytes(data)
    res = CliRunner().invoke(main, ["import", name, "--data-dir", "data", *args])
    assert res.exit_code == 2 and why in res.output and "Traceback" not in res.output
    assert res.exception is None or isinstance(res.exception, SystemExit)
    if (tmp_path / "data" / "codraft.db").exists():
        assert Store(tmp_path / "data" / "codraft.db").list_documents() == []


def test_cli_import_refuses_a_huge_file_before_reading_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(importers, "MAX_IMPORT_BYTES", 100)
    (tmp_path / "big.md").write_bytes(b"word " * 100)
    res = CliRunner().invoke(main, ["import", "big.md", "--data-dir", "data"])
    assert res.exit_code == 2 and "too large to import" in res.output
