"""Office and motion exports: DOCX, PPTX, GIF and MP4.

These build on the same render as PDF/PNG. The renderer reports where every block
landed on every page (``Renderer.structure``), with element screenshots for anything
that isn't plain text, so:

* **PPTX**: one slide per page, editable text boxes at the exact positions the
  paginator chose; charts, images, tables and split text fragments become pictures.
* **DOCX**: a flowing Word document (Word re-paginates): native headings,
  paragraphs, lists, tables, page size, margins and columns; charts and images are
  pictures.
* **GIF / MP4**: a page-by-page slideshow of the rendered pages. MP4 needs the
  ``[video]`` extra (imageio + a bundled ffmpeg).
"""
from __future__ import annotations

import io
import re
from typing import Any

from .fonts import family_of
from .pages import page_mm
from .store import Store
from .themes import effective_theme, resolve_fill


def qn(tag: str) -> str:
    from docx.oxml.ns import qn as _qn

    return _qn(tag)

EMU_PER_PX = 9525  # 914400 EMU/inch at 96 px/inch


# ------------------------------------------------------------------ inline markdown
_INLINE = re.compile(
    r"(\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|`[^`]+`|\[[^\]]+\]\([^)]+\)|~~[^~]+~~"
    # Superscript and subscript, as the editor reads them: x^2^, H~2~O (attached to the text
    # before them, no spaces inside) and the <sup> / <sub> tags.
    r"|(?<=\S)\^[^\s^\[\]]{1,40}\^|(?<=[^\s~])~[^\s~]{1,40}~(?!~)|<su[pb]>[^<]*</su[pb]>)")


def md_runs(text: str) -> list[tuple[str, dict]]:
    """Split one line of Markdown into (text, style) runs: bold, italic, code, link, strike,
    superscript and subscript."""
    runs: list[tuple[str, dict]] = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith(("**", "__")) and len(part) > 4:
            runs.append((part[2:-2], {"bold": True}))
        elif part.startswith("~~"):
            runs.append((part[2:-2], {"strike": True}))
        elif part.startswith(("<sup>", "<sub>")):
            runs.append((part[5:-6], {"sup" if part[3] == "p" else "sub": True}))
        elif part[0] == "^" and part[-1] == "^" and len(part) > 2:
            runs.append((part[1:-1], {"sup": True}))
        elif part[0] == "~" and part[-1] == "~" and len(part) > 2:
            runs.append((part[1:-1], {"sub": True}))
        elif part.startswith("`"):
            runs.append((part[1:-1], {"code": True}))
        elif part.startswith("["):
            m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part)
            runs.append((m.group(1), {"link": m.group(2)}) if m else (part, {}))
        elif part[0] in "*_" and part[-1] == part[0] and len(part) > 2:
            runs.append((part[1:-1], {"italic": True}))
        else:
            runs.append((part, {}))
    return runs


_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


def _table_cells(line: str) -> list[str]:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def md_table(text: str) -> tuple[list[list[str]], list[str | None]]:
    """A Markdown table (the text of a ``table`` item from :func:`md_blocks`) as rows, header
    first, and each column's alignment: left, center, right or None."""
    lines = text.splitlines()
    align: list[str | None] = []
    for c in _table_cells(lines[1]):
        left, right = c.startswith(":"), c.endswith(":")
        align.append("center" if left and right else "right" if right else "left" if left else None)
    width = len(align)
    rows = [(_table_cells(ln) + [""] * width)[:width] for ln in [lines[0], *lines[2:]]]
    return rows, align


def md_blocks(md: str) -> list[tuple[str, str]]:
    """Coarse Markdown block parse -> [(kind, text)], kind in p, h1-h3, ul, ol, quote, code,
    hr, table (its lines, for :func:`md_table`)."""
    out: list[tuple[str, str]] = []
    para: list[str] = []
    code: list[str] | None = None
    table: list[str] | None = None

    def flush():
        if para:
            out.append(("p", " ".join(s.strip() for s in para)))
            para.clear()

    lines = (md or "").splitlines()
    for i, line in enumerate(lines):
        if table is not None:
            if line.strip() and "|" in line:
                table.append(line)
                continue
            out.append(("table", "\n".join(table)))
            table = None
        if (code is None and "|" in line and i + 1 < len(lines) and "-" in lines[i + 1]
                and _TABLE_RULE.match(lines[i + 1])
                and len(_table_cells(line)) == len(_table_cells(lines[i + 1]))):
            flush()
            table = [line]
            continue
        if code is not None:
            if line.strip().startswith("```"):
                out.append(("code", "\n".join(code)))
                code = None
            else:
                code.append(line)
            continue
        s = line.strip()
        if s.startswith("```"):
            flush()
            code = []
        elif not s:
            flush()
        elif re.match(r"^#{1,6} ", s):
            flush()
            level = min(3, len(s) - len(s.lstrip("#")))
            out.append((f"h{level}", s.lstrip("#").strip()))
        elif re.match(r"^[-*+] ", s):
            flush()
            out.append(("ul", s[2:].strip()))
        elif re.match(r"^\d+[.)] ", s):
            flush()
            out.append(("ol", re.sub(r"^\d+[.)] ", "", s)))
        elif s.startswith(">"):
            flush()
            out.append(("quote", s.lstrip("> ").strip()))
        elif re.fullmatch(r"[-*_]{3,}", s):
            flush()
            out.append(("hr", ""))
        else:
            para.append(s)
    flush()
    if table is not None:
        out.append(("table", "\n".join(table)))
    if code is not None:
        out.append(("code", "\n".join(code)))
    return out


def plain(md: str) -> str:
    return " ".join(t for t, _ in md_runs(md))


# ----------------------------------------------------------- tracked changes (gh #65)
# A redline is the Word export with the changes written as Word's own tracked changes
# (``w:ins`` / ``w:del``), so a reviewer outside Codraft gets Word's Accept / Reject.
# Paragraph-like Markdown is diffed word by word on the formatted runs; anything else
# (a table, a chart, a row of cells) is shown as the old block deleted and the new one
# inserted.

# The Markdown blocks diffed word by word (anything else is replaced whole).
_DIFF_KINDS = ("p", "ul", "ol", "quote", "h1", "h2", "h3")
# Authors that name a person only as "whoever was editing": shown as "Author".
_ANON = ("", "user", "author", "human", "template", "auto")


def redline_author(who: str | None, agent_name: str = "Agent") -> str:
    """The name a tracked change is attributed to in Word."""
    w = str(who or "").strip()
    if w.lower() == "agent":
        return agent_name or "Agent"
    return "Author" if w.lower() in _ANON else w[:60]


def _word_date(at: float | None) -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at if at else time.time()))


class _Revisions:
    """Writes revision marks. Every mark gets its own ``w:id``."""

    def __init__(self) -> None:
        self.n = 100

    def mark(self, op: str, author: str, date: str) -> Any:
        from docx.oxml import OxmlElement

        self.n += 1
        el = OxmlElement(f"w:{op}")
        el.set(qn("w:id"), str(self.n))
        el.set(qn("w:author"), author)
        el.set(qn("w:date"), date)
        return el

    def run(self, r: Any, op: str, author: str, date: str) -> None:
        """Wrap one ``w:r`` in ``w:ins`` / ``w:del`` (deleted text becomes ``w:delText``)."""
        parent = r.getparent()
        if parent is None or parent.tag in (qn("w:ins"), qn("w:del"), qn("w:moveFrom"), qn("w:moveTo")):
            return
        wrap = self.mark(op, author, date)
        parent.insert(parent.index(r), wrap)
        wrap.append(r)
        if op == "del":
            for t in r.iter(qn("w:t")):
                t.tag = qn("w:delText")
            for t in r.iter(qn("w:instrText")):
                t.tag = qn("w:delInstrText")

    def paragraph_mark(self, p: Any, op: str, author: str, date: str) -> None:
        """Mark a paragraph's end as inserted or deleted, so the whole paragraph is."""
        from docx.oxml import OxmlElement

        ppr = p.get_or_add_pPr()
        rpr = ppr.find(qn("w:rPr"))
        if rpr is None:
            rpr = OxmlElement("w:rPr")
            later = [c for c in ppr if c.tag in (qn("w:sectPr"), qn("w:pPrChange"))]
            if later:
                later[0].addprevious(rpr)
            else:
                ppr.append(rpr)
        rpr.insert(0, self.mark(op, author, date))

    def _range(self, els: list, kind: str, name: str, author: str, date: str) -> None:
        """``w:<kind>RangeStart`` before the first paragraph and ``RangeEnd`` after the last,
        in the body as Word writes them, so the paragraph marks move too. The two places of
        one move share the name."""
        from docx.oxml import OxmlElement

        self.n += 1
        start, end = OxmlElement(f"w:{kind}RangeStart"), OxmlElement(f"w:{kind}RangeEnd")
        for k, v in (("id", str(self.n)), ("author", author), ("date", date), ("name", name)):
            start.set(qn(f"w:{k}"), v)
        end.set(qn("w:id"), str(self.n))
        els[0].addprevious(start)
        els[-1].addnext(end)

    @staticmethod
    def _by(m: Any, tag: str, author: str, date: str) -> None:
        m.tag = qn(tag)
        m.set(qn("w:author"), author)
        m.set(qn("w:date"), date)

    def move_from(self, els: list, name: str, author: str, date: str) -> None:
        """Paragraphs drawn as deleted become the place a move came from."""
        for el in els:
            for m in list(el.iter(qn("w:del"))):
                self._by(m, "w:moveFrom", author, date)
            for t in el.iter(qn("w:delText")):
                t.tag = qn("w:t")
            for t in el.iter(qn("w:delInstrText")):
                t.tag = qn("w:instrText")
        self._range(els, "moveFrom", name, author, date)

    def move_to(self, els: list, name: str, author: str, date: str, edited: bool = False) -> None:
        """Paragraphs drawn as inserted (or, for a block edited as well as moved, as the
        word diff) become the place a move went to. In a diff the words left unmarked are
        the moved ones and become ``w:moveTo``. Inserted words stay ``w:ins`` and deleted
        ones ``w:del``, as Word writes a move that was edited afterwards: rejecting the move
        carries this place's words back, deleted ones included."""
        for el in els:
            if edited:
                for r in list(el.iter(qn("w:r"))):
                    self.run(r, "moveTo", author, date)  # skips runs already marked
            else:
                for m in list(el.iter(qn("w:ins"))):
                    if m.getparent().tag != qn("w:rPr"):
                        self._by(m, "w:moveTo", author, date)
            pm = _mark_of(el, "ins")
            if pm is not None and not edited:
                self._by(pm, "w:moveTo", author, date)
            elif pm is None and _mark_of(el, "moveTo") is None and _mark_of(el, "del") is None:
                self.paragraph_mark(el, "moveTo", author, date)
        self._range(els, "moveTo", name, author, date)

    def elements(self, els: list, op: str, author: str, date: str) -> None:
        """Mark whole body elements (paragraphs, tables) as inserted or deleted."""
        for el in els:
            for r in list(el.iter(qn("w:r"))):
                self.run(r, op, author, date)
            if el.tag == qn("w:p"):
                self.paragraph_mark(el, op, author, date)
            for tr in el.iter(qn("w:tr")):
                tr.get_or_add_trPr().append(self.mark(op, author, date))


def _mark_of(p: Any, op: str) -> Any:
    """The revision on a paragraph's mark (``w:pPr/w:rPr/w:<op>``), or None."""
    ppr = p.find(qn("w:pPr"))
    rpr = ppr.find(qn("w:rPr")) if ppr is not None else None
    return rpr.find(qn(f"w:{op}")) if rpr is not None else None


def _tokens(runs: list[tuple[str, dict]]) -> list[tuple[tuple, str, dict]]:
    """Formatted runs as words (with the space after each): ``(key, text, style)``. The key
    leaves the space out, so "brief" at the end of a heading matches "brief " before an
    added word."""
    out = []
    for text, st in runs:
        style = tuple(sorted((k, str(v)) for k, v in st.items()))
        for tok in re.findall(r"\S+\s*|\s+", text):
            out.append(((tok.strip() or tok, style), tok, st))
    return out


# -------------------------------------------------------------------------- DOCX
def to_docx(store: Store, doc_id: str, pictures: dict[str, bytes], references: list[dict],
            plan: list[dict] | None = None, agent_name: str = "Agent") -> bytes:
    """The document as Word. With ``plan`` (``Store.redline_since`` /
    ``Store.redline_pending``) it is a redline: the changes are Word tracked changes."""
    from difflib import SequenceMatcher

    import docx
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor

    from .refs import format_ref, numbers_for, replace_cites

    doc = store.get_document(doc_id)
    s = doc["settings"]
    blocks = store.list_blocks(doc_id)
    numbers = numbers_for(blocks)

    def cited(md: Any) -> str:
        """[@key] -> [3], the source's number in the reference list."""
        return replace_cites(str(md if md is not None else ""), numbers)

    d = docx.Document()
    sec = d.sections[0]
    w, h = page_mm(s)
    sec.page_width, sec.page_height = Mm(w), Mm(h)
    sec.orientation = WD_ORIENT.LANDSCAPE if s.get("orientation") == "landscape" else WD_ORIENT.PORTRAIT
    m = s.get("margins", {})
    sec.top_margin, sec.right_margin = Mm(m.get("top", 20)), Mm(m.get("right", 20))
    sec.bottom_margin, sec.left_margin = Mm(m.get("bottom", 20)), Mm(m.get("left", 20))
    d.core_properties.title = doc["title"]
    content_w_mm = w - m.get("left", 20) - m.get("right", 20)
    theme = effective_theme(s)
    _docx_styles(d, theme)

    def set_columns(section, n: int):
        cols = section._sectPr.find(qn("w:cols"))
        if cols is None:
            cols = section._sectPr.makeelement(qn("w:cols"), {})
            section._sectPr.append(cols)
        cols.set(qn("w:num"), str(n))
        cols.set(qn("w:space"), str(int(s.get("column_gap", 8) / 25.4 * 1440)))

    def add_runs(par, text: str, base: dict | None = None):
        for t, st in md_runs(text):
            style_run(par.add_run(t), {**(base or {}), **st})

    def style_run(r, st: dict):
        r.bold = st.get("bold") or None
        r.italic = st.get("italic") or None
        if st.get("strike"):
            r.font.strike = True
        if st.get("sup"):
            r.font.superscript = True
        if st.get("sub"):
            r.font.subscript = True
        if st.get("code"):
            r.font.name = "Consolas"
        if st.get("link"):
            r.font.color.rgb = _rgb(theme.get("color_accent")) or RGBColor(0x1A, 0x4D, 0x8F)
            r.underline = True

    def add_table(rows: list, align: list | None = None, header: bool = True, total: bool = False):
        """A Word table: bold header row (and total row), each column aligned as the document
        has it. A cell is one line of Markdown. The header row repeats on every page."""
        n = max((len(r) for r in rows), default=0)
        if not n:
            return
        sides = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
                 "right": WD_ALIGN_PARAGRAPH.RIGHT}
        tbl = d.add_table(rows=len(rows), cols=n)
        tbl.style = "Table Grid"
        for i, r in enumerate(rows):
            for j in range(n):
                cell = tbl.cell(i, j)
                cell.text = ""
                par = cell.paragraphs[0]
                strong = (i == 0 and header) or (total and i == len(rows) - 1 and len(rows) > 2)
                add_runs(par, cited(r[j]) if j < len(r) else "", {"bold": strong})
                side = align[j] if isinstance(align, list) and j < len(align) else None
                if side in sides:
                    par.alignment = sides[side]
        if header and len(rows) > 1:
            tr_pr = tbl.rows[0]._tr.get_or_add_trPr()
            tr_pr.append(tr_pr.makeelement(qn("w:tblHeader"), {qn("w:val"): "true"}))

    def add_markdown(md: str, style_quote=False):
        for kind, text in md_blocks(cited(md)):
            add_md_block(kind, text, style_quote)

    def md_paragraph(kind: str, style_quote: bool):
        """An empty paragraph styled for one of the ``_DIFF_KINDS``."""
        if kind.startswith("h"):
            return d.add_heading("", level=min(3, int(kind[1]) + 1))
        if kind == "ul":
            return d.add_paragraph(style="List Bullet")
        if kind == "ol":
            return d.add_paragraph(style="List Number")
        if kind == "quote" or style_quote:
            return d.add_paragraph(style="Quote")
        return d.add_paragraph()

    def add_md_block(kind: str, text: str, style_quote=False):
        if kind == "table":
            add_table(*md_table(text))
        elif kind.startswith("h"):
            d.add_heading(text, level=min(3, int(kind[1]) + 1))
        elif kind in ("ul", "ol") or kind == "quote" or style_quote:
            add_runs(md_paragraph(kind, style_quote), text)
        elif kind == "code":
            p = d.add_paragraph()
            r = p.add_run(text)
            r.font.name = "Consolas"
            r.font.size = Pt(9)
        elif kind == "hr":
            d.add_paragraph("―" * 20)
        else:
            add_runs(d.add_paragraph(), text)

    def hex6(color: str | None) -> str | None:
        """A colour as Word wants it (RRGGBB), or None when it isn't a hex colour."""
        c = (color or "").strip().lstrip("#")
        if re.fullmatch(r"[0-9a-fA-F]{3}", c):
            c = "".join(ch * 2 for ch in c)
        return c.upper() if re.fullmatch(r"[0-9a-fA-F]{6}", c) else None

    def block_fill(b: dict) -> tuple[str, bool] | None:
        """``(RRGGBB, dark)`` for a block drawn on a fill: its own, or the accent for a band."""
        lay = b["layout"]
        got = resolve_fill(lay.get("fill") or ("accent" if lay.get("variant") == "band" else None), theme)
        return (hex6(got[0]), got[1]) if got and hex6(got[0]) else None

    def shade(pr, color: str):
        """Background shading on a paragraph's or a table cell's properties element."""
        shd = pr.makeelement(qn("w:shd"), {qn("w:val"): "clear", qn("w:color"): "auto", qn("w:fill"): color})
        pr.append(shd)

    def ink(par, dark: bool):
        from docx.text.run import Run

        # Every run, tracked ones (inside w:ins / w:del) included.
        for r in (Run(el, par) for el in par._p.iter(qn("w:r"))):
            r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF) if dark else RGBColor(0x1D, 0x1D, 0x1F)

    def shade_new(before: list, fill: tuple[str, bool] | None):
        """Shade the paragraphs added to the body since ``before`` was taken."""
        if not fill:
            return
        from docx.text.paragraph import Paragraph

        seen = {id(el) for el in before}
        for el in list(d.element.body):
            if id(el) in seen or el.tag != qn("w:p"):
                continue
            par = Paragraph(el, d)
            shade(par._p.get_or_add_pPr(), fill[0])
            ink(par, fill[1])

    def cell_markdown(cell, md: str, dark: bool | None = None):
        """Markdown into a table cell (a row block's cell): paragraphs, bold headings, bullets."""
        first = True
        for kind, text in md_blocks(cited(md)):
            lines = ["  |  ".join(r) for r in md_table(text)[0]] if kind == "table" else [text]
            for line in lines:
                par = cell.paragraphs[0] if first else cell.add_paragraph()
                first = False
                add_runs(par, ("• " if kind == "ul" else "") + line, {"bold": True} if kind.startswith("h") else None)
                if dark is not None:
                    ink(par, dark)

    def side_by_side(n: int, widths: list | None, full: bool):
        """A borderless one-row table: how Word shows cells that sit side by side."""
        tbl = d.add_table(rows=1, cols=n)
        total = content_w_mm if full or columns == 1 else (content_w_mm - (columns - 1) * s.get("column_gap", 8)) / columns
        ok = isinstance(widths, list) and len(widths) == n and all(
            isinstance(w, (int, float)) and not isinstance(w, bool) and w > 0 for w in widths)
        share = [w / sum(widths) for w in widths] if ok else [1 / n] * n
        tbl.autofit = False
        for j in range(n):
            tbl.cell(0, j).width = Mm(total * share[j])
        return tbl

    def add_row(b: dict):
        c = b["content"]
        cells = [x.get("markdown", "") if isinstance(x, dict) else str(x or "") for x in (c.get("cells") or [])]
        if not cells:
            return
        fill = block_fill(b)
        if not fill and b["layout"].get("variant") == "cards" and hex6(theme.get("color_callout")):
            fill = (hex6(theme.get("color_callout")), False)
        tbl = side_by_side(len(cells), c.get("widths"), b["layout"].get("span") == "full")
        for j, md in enumerate(cells):
            cell = tbl.cell(0, j)
            cell_markdown(cell, md, fill[1] if fill else None)
            if fill:
                shade(cell._tc.get_or_add_tcPr(), fill[0])
        d.add_paragraph()

    def add_stat(b: dict):
        items = [i for i in (b["content"].get("items") or []) if isinstance(i, dict)]
        if not items:
            return
        fill = block_fill(b)
        if not fill and b["layout"].get("variant") != "plain":
            tint = hex6(theme.get("color_figure") or theme.get("color_callout"))
            fill = (tint, False) if tint else None
        body_pt = float(theme.get("font_size", 15)) * 0.75
        accent = _rgb(theme.get("color_heading") or theme.get("color_accent"))
        tbl = side_by_side(len(items), None, b["layout"].get("span") == "full")
        for j, it in enumerate(items):
            cell = tbl.cell(0, j)
            value = cell.paragraphs[0]
            value.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = value.add_run(str(it.get("value") or ""))
            r.bold = True
            r.font.size = Pt(round(body_pt * 2.0, 1))
            if b["layout"].get("fill"):
                ink(value, fill[1] if fill else False)
            elif accent is not None:
                r.font.color.rgb = accent
            if it.get("label"):
                label = cell.add_paragraph()
                label.alignment = WD_ALIGN_PARAGRAPH.CENTER
                lr = label.add_run(str(it["label"]))
                lr.font.size = Pt(round(body_pt * 0.82, 1))
                if b["layout"].get("fill") and fill:
                    ink(label, fill[1])
            if fill:
                shade(cell._tc.get_or_add_tcPr(), fill[0])
        d.add_paragraph()

    def picture(bid: str, caption: str, kind: str = "image", old: bool = False):
        data = None if old else pictures.get(bid)
        if data:
            d.add_picture(io.BytesIO(data), width=Mm(min(content_w_mm, 170 if s.get("columns", 1) == 1 else content_w_mm / s["columns"])))
        elif plan is not None:
            # A redline has no picture of a block as it was, or of one not yet accepted: say what it is.
            d.add_paragraph(f"[{kind}]")
        if caption:
            p = d.add_paragraph(caption)
            p.style = d.styles["Caption"]

    columns = int(s.get("columns", 1)) if s.get("mode") == "paged" else 1
    segments = plan if plan is not None else [{"op": "same", "block": b} for b in blocks]
    cover = [g for g in segments if g["block"]["layout"].get("role") == "cover"]
    flow = [g for g in segments if g["block"]["layout"].get("role", "flow") == "flow"]
    back = [g for g in segments if g["block"]["layout"].get("role") == "back"]

    def heading_level(b: dict) -> int:
        if b["layout"].get("role") == "cover":
            return 0
        return max(0, min(3, int(b["content"].get("level", 1))))

    def band(par, b: dict):
        fill = block_fill(b)
        if fill:  # a band: the strip behind the heading, and text that reads on it
            shade(par._p.get_or_add_pPr(), fill[0])
            ink(par, fill[1])

    def emit(b: dict, old: bool = False):
        c, t = b["content"], b["type"]
        if t == "heading":
            band(d.add_heading(c.get("text", ""), level=heading_level(b)), b)
        elif t == "text":
            add_markdown(c.get("markdown", ""))
        elif t == "callout":
            before = list(d.element.body)
            add_markdown(c.get("markdown", ""), style_quote=True)
            shade_new(before, block_fill(b))
        elif t == "row":
            add_row(b)
        elif t == "stat":
            add_stat(b)
        elif t == "table":
            add_table([r for r in (c.get("rows") or []) if isinstance(r, list)], c.get("align"),
                      bool(c.get("header", True)), c.get("total") is True)
            if c.get("caption"):
                d.add_paragraph(c["caption"]).style = d.styles["Caption"]
        elif t in ("image", "chart", "video"):
            picture(b["id"], c.get("caption", ""), t, old)
        elif t == "divider":
            d.add_paragraph("―" * 20)
        elif t == "references":
            d.add_heading(c.get("title", "References"), level=2)
            for r in references:
                # Authors (Year). Title. Publisher. URL. The private note is not printed.
                p = d.add_paragraph(style="List Number")
                p.add_run(format_ref(r))
        elif t == "page-break":
            d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    revs = _Revisions()

    def added_since(before: list) -> list:
        seen = {id(el) for el in before}
        return [el for el in list(d.element.body) if id(el) not in seen and el.tag != qn("w:sectPr")]

    def whole(b: dict, op: str, who: tuple[str, str], old: bool = False):
        """The block, all of it inserted or deleted."""
        before = list(d.element.body)
        emit(b, old=old)
        revs.elements(added_since(before), op, *who)

    def diff_par(par, a: list, b: list) -> list:
        """Runs into ``par``: the words of ``a`` (formatted runs) changed into those of ``b``.
        Returns the runs to mark, as ``(w:r, op)``."""
        ta, tb = _tokens(a), _tokens(b)
        marks = []

        def put(toks, op):
            i = 0
            while i < len(toks):
                j = i
                while j + 1 < len(toks) and toks[j + 1][0][1] == toks[i][0][1]:
                    j += 1
                r = par.add_run("".join(t[1] for t in toks[i:j + 1]))
                style_run(r, toks[i][2])
                if op:
                    marks.append((r._r, op))
                i = j + 1

        sm = SequenceMatcher(None, [t[0] for t in ta], [t[0] for t in tb], autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                put(tb[j1:j2], None)
                continue
            put(ta[i1:i2], "del")
            put(tb[j1:j2], "ins")
        return marks

    def md_runs_of(kind: str, text: str) -> list:
        return [(text, {})] if kind.startswith("h") else md_runs(text)

    def edit_markdown(old_md: str, new_md: str, quote: bool, who: tuple[str, str]):
        """A text or callout block's Markdown, changed: paragraphs that stayed are plain,
        paragraphs of the same kind are diffed word by word, the rest inserted or deleted."""
        a, b = md_blocks(cited(old_md)), md_blocks(cited(new_md))

        def one(kind, text, op):
            before = list(d.element.body)
            add_md_block(kind, text, quote)
            if op:
                revs.elements(added_since(before), op, *who)

        sm = SequenceMatcher(None, a, b, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for kind, text in b[j1:j2]:
                    one(kind, text, None)
                continue
            olds, news = a[i1:i2], b[j1:j2]
            for k in range(max(len(olds), len(news))):
                o = olds[k] if k < len(olds) else None
                n = news[k] if k < len(news) else None
                if o and n and o[0] == n[0] and o[0] in _DIFF_KINDS:
                    par = md_paragraph(n[0], quote)
                    for r, op in diff_par(par, md_runs_of(*o), md_runs_of(*n)):
                        revs.run(r, op, *who)
                    continue
                if o:
                    one(*o, "del")
                if n:
                    one(*n, "ins")

    def word_diffable(then: dict, b: dict) -> bool:
        t = b["type"]
        return t in ("text", "callout") or (t == "heading" and heading_level(then) == heading_level(b))

    def edit(b: dict, before_content: dict, who: tuple[str, str]):
        then = {**b, "content": before_content or {}}
        t = b["type"]
        if not word_diffable(then, b):
            whole(then, "del", who, old=True)
            whole(b, "ins", who)
        elif t == "heading":
            par = d.add_heading("", level=heading_level(b))
            marks = diff_par(par, [(str(then["content"].get("text", "")), {})],
                             [(str(b["content"].get("text", "")), {})])
            band(par, b)
            for r, mop in marks:
                revs.run(r, mop, *who)
        else:
            before = list(d.element.body)
            edit_markdown(str(then["content"].get("markdown", "")), str(b["content"].get("markdown", "")),
                          t == "callout", who)
            if t == "callout":
                shade_new(before, block_fill(b))

    # A moved block is written as Word's move marks (w:moveFrom where it was, w:moveTo where
    # it is now), so Word shows it as moved. Both places are drawn first as a deletion and
    # an insertion (or, if it was edited too, as the word diff), then converted once the
    # whole document is drawn and both places are known.
    moves: dict[str, dict] = {}

    def put_move(g: dict, who: tuple[str, str]):
        b, side = g["block"], g["move"]
        rec = moves.setdefault(str(g.get("move_id") or b["id"]), {})
        before = list(d.element.body)
        if side == "from":
            whole(b, "del", who, old=True)
            rec["from"] = (added_since(before), who)
            return
        then = g.get("before")
        if then is None or then == b["content"]:
            whole(b, "ins", who)
            mode = "ins"
        elif word_diffable({**b, "content": then}, b):
            edit(b, then, who)
            mode = "edit"
        else:  # edited past a word diff: a deletion there and an insertion here
            whole(b, "ins", who)
            mode = "replaced"
        rec["to"] = (added_since(before), who, mode, b)

    def put_segment(g: dict):
        b, op = g["block"], g.get("op", "same")
        if op == "same":
            emit(b)
            return
        who = (redline_author(g.get("author"), agent_name), _word_date(g.get("at")))
        if g.get("move") in ("from", "to") and op in ("ins", "del"):
            put_move(g, who)
            return
        if op in ("ins", "del"):
            whole(b, op, who, old=op == "del")
            return
        edit(b, g.get("before") or {}, who)

    def finish_moves():
        n = 0
        for rec in moves.values():
            if "from" not in rec or "to" not in rec:
                continue
            fr, mover = rec["from"]
            to, editor, mode, b = rec["to"]
            if mode == "replaced" or not fr or not to:
                continue
            if any(el.tag != qn("w:p") or el.find(".//" + qn("w:tbl")) is not None for el in fr + to):
                # Tables have no move marks, so this move stays a deletion and an insertion.
                # A diff here would bring the old words back twice on Reject: redraw it whole.
                if mode == "edit":
                    before = list(d.element.body)
                    whole(b, "ins", editor)
                    for el in added_since(before):
                        to[0].addprevious(el)
                    for el in to:
                        el.getparent().remove(el)
                continue
            n += 1
            name = f"move{n}"
            revs.move_from(fr, name, *mover)
            revs.move_to(to, name, *mover, edited=mode == "edit")

    separate_cover = bool(cover) and (s.get("cover") or {}).get("enabled", True)
    for g in cover:
        put_segment(g)
    if separate_cover:
        d.add_section()  # new page; flow section gets the columns
    if columns > 1:
        set_columns(d.sections[-1], columns)
    flow_section = d.sections[-1]
    if separate_cover:
        _clear_header_footer(d.sections[0])
    _header_footer(flow_section, s, doc["title"], content_w_mm)
    for g in flow:
        put_segment(g)
    if back:
        from docx.enum.section import WD_SECTION

        sec_back = d.add_section(WD_SECTION.NEW_PAGE)
        set_columns(sec_back, 1)
        _clear_header_footer(sec_back, unlink=True)
        for g in back:
            put_segment(g)
    finish_moves()
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _rgb(color: str | None) -> Any:
    from docx.shared import RGBColor

    m = re.fullmatch(r"#([0-9a-fA-F]{6})", (color or "").strip())
    return RGBColor.from_string(m.group(1).upper()) if m else None


def _docx_styles(d: Any, theme: dict) -> None:
    """The document's fonts, sizes and colours on Word's Normal, Title and Heading styles."""
    from docx.shared import Pt

    body_pt = float(theme.get("font_size", 15)) * 0.75
    scale = float(theme.get("heading_scale", 1.25))
    body, head = family_of(theme.get("font_body", "")), family_of(theme.get("font_heading", ""))
    display = family_of(theme.get("font_display") or "") or head
    normal = d.styles["Normal"]
    normal.font.name, normal.font.size = body or None, Pt(round(body_pt, 1))
    if _rgb(theme.get("color_text")):
        normal.font.color.rgb = _rgb(theme.get("color_text"))
    heading_color = theme.get("color_heading")
    for name, size, color, family in (
        ("Title", body_pt * scale ** 4, heading_color or theme.get("color_text"), display),
        ("Heading 1", body_pt * scale ** 3, heading_color or theme.get("color_text"), head),
        ("Heading 2", body_pt * scale ** 2, heading_color or theme.get("color_accent"), head),
        ("Heading 3", body_pt * scale, heading_color or theme.get("color_text"), head),
    ):
        try:
            st = d.styles[name]
        except KeyError:
            continue
        st.font.name, st.font.size = family or None, Pt(round(size, 1))
        st.font.bold = int(theme.get("heading_weight", 700)) >= 600
        if _rgb(color):
            st.font.color.rgb = _rgb(color)
        rpr = st.element.get_or_add_rPr()
        fonts_el = rpr.find(qn("w:rFonts"))
        if fonts_el is not None:  # drop theme-font links so the explicit family wins
            for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
                fonts_el.attrib.pop(qn(attr), None)


def _field(run: Any, instr: str) -> None:
    """A Word field (PAGE, NUMPAGES) inside a run."""
    from docx.oxml import OxmlElement

    for kind, text in (("begin", None), (None, instr), ("separate", None), (None, "1"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        elif text == instr:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = f" {instr} "
        else:
            el = OxmlElement("w:t")
            el.text = text
        run._r.append(el)


def _clear_header_footer(section: Any, unlink: bool = False) -> None:
    for part in (section.header, section.footer):
        if unlink:
            part.is_linked_to_previous = False
        for par in part.paragraphs:
            for r in list(par.runs):
                r._r.getparent().remove(r._r)


def _header_footer(section: Any, s: dict, title: str, content_w_mm: float) -> None:
    """The running header and footer: left/centre/right on tab stops, {page} and {pages}
    as live Word fields."""
    from datetime import datetime

    from docx.enum.text import WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.shared import Mm, Pt

    first_hidden = False
    for part_name in ("header", "footer"):
        hf = s.get(part_name) or {}
        slots = [hf.get(k) or "" for k in ("left", "center", "right")]
        target = getattr(section, part_name)
        target.is_linked_to_previous = False
        if not any(slots):
            continue
        first_hidden = first_hidden or not hf.get("first_page", True)
        par = target.paragraphs[0]
        par.paragraph_format.tab_stops.add_tab_stop(Mm(content_w_mm / 2), WD_TAB_ALIGNMENT.CENTER)
        par.paragraph_format.tab_stops.add_tab_stop(Mm(content_w_mm), WD_TAB_ALIGNMENT.RIGHT)
        for i, text in enumerate(slots):
            if i:
                par.add_run("\t")
            text = text.replace("{title}", title).replace("{year}", str(datetime.now().year))
            for piece in re.split(r"(\{page\}|\{pages\})", text):
                if piece in ("{page}", "{pages}"):
                    _field(par.add_run(), "PAGE" if piece == "{page}" else "NUMPAGES")
                elif piece:
                    par.add_run(piece)
        for r in par.runs:
            r.font.size = Pt(8)
        if hf.get("rule"):
            ppr = par._p.get_or_add_pPr()
            bdr = OxmlElement("w:pBdr")
            edge = OxmlElement("w:bottom" if part_name == "header" else "w:top")
            for k, v in (("w:val", "single"), ("w:sz", "4"), ("w:space", "4"), ("w:color", "999999")):
                edge.set(qn(k), v)
            bdr.append(edge)
            ppr.append(bdr)
    if first_hidden:
        section.different_first_page_header_footer = True


# -------------------------------------------------------------------------- PPTX
def to_pptx(store: Store, doc_id: str, structure: dict) -> bytes:
    from pptx import Presentation
    from pptx.util import Emu, Pt

    from .refs import numbers_for, replace_cites

    doc = store.get_document(doc_id)
    listed = store.list_blocks(doc_id)
    blocks = {b["id"]: b for b in listed}
    numbers = numbers_for(listed)
    prs = Presentation()
    pw, ph = structure["page_px"]
    prs.slide_width, prs.slide_height = Emu(int(pw * EMU_PER_PX)), Emu(int(ph * EMU_PER_PX))
    prs.core_properties.title = doc["title"]
    blank = prs.slide_layouts[6]
    theme = structure.get("theme", {})
    body_pt = float(theme.get("font_size", 15)) * 0.75
    scale = float(theme.get("heading_scale", 1.25))

    for page in structure["pages"]:
        slide = prs.slides.add_slide(blank)
        for it in page["items"]:
            b = blocks.get(it["id"])
            x, y, w, h = (Emu(int(v * EMU_PER_PX)) for v in (it["x"], it["y"], it["w"], it["h"]))
            textual = (b is not None and b["type"] in ("text", "heading", "callout") and not it["fragment"]
                       and not it.get("png"))
            if textual:
                tb = slide.shapes.add_textbox(x, y, w, h)
                tf = tb.text_frame
                tf.word_wrap = True
                for attr in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
                    setattr(tf, attr, 0)
                first = True
                if b["type"] == "heading":
                    lvl = int(b["content"].get("level", 1))
                    items = [("h", b["content"].get("text", ""), body_pt * scale ** (4 - lvl))]
                else:
                    parts = []
                    for k, t in md_blocks(replace_cites(b["content"].get("markdown", ""), numbers)):
                        if k == "table":  # a text box has no grid: one line per row
                            parts.extend(("p", "  |  ".join(r)) for r in md_table(t)[0])
                        else:
                            parts.append((k, t))
                    items = [(k, t, body_pt * (scale ** (4 - int(k[1])) if k in ("h1", "h2", "h3") else 1.0))
                             for k, t in parts]
                for kind, text, size in items:
                    para = tf.paragraphs[0] if first else tf.add_paragraph()
                    first = False
                    prefix = "• " if kind == "ul" else ""
                    for t, st in md_runs(prefix + text):
                        r = para.add_run()
                        r.text = t
                        r.font.size = Pt(size)
                        if st.get("sup") or st.get("sub"):  # DrawingML: baseline shift in 1/1000 %
                            r.font._element.set("baseline", "30000" if st.get("sup") else "-25000")
                        r.font.bold = True if (st.get("bold") or kind.startswith("h")) else None
                        r.font.italic = st.get("italic") or None
                    para.space_after = Pt(size * 0.5)
            elif it.get("png"):
                slide.shapes.add_picture(io.BytesIO(it["png"]), x, y, w, h)
        if page.get("notes"):
            slide.notes_slide.notes_text_frame.text = page["notes"]
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------- GIF / MP4
def to_gif(pages: list[bytes], seconds_per_page: float = 2.5) -> bytes:
    from PIL import Image

    frames = [Image.open(io.BytesIO(p)).convert("RGB") for p in pages]
    if not frames:
        raise ValueError("no pages to animate")
    pal = [f.convert("P", palette=Image.Palette.ADAPTIVE, colors=255) for f in frames]
    buf = io.BytesIO()
    pal[0].save(buf, format="GIF", save_all=True, append_images=pal[1:], loop=0,
                duration=int(seconds_per_page * 1000), optimize=True)
    return buf.getvalue()


def to_mp4(pages: list[bytes], seconds_per_page: float = 3.0, fps: int = 10) -> bytes:
    """A page-by-page slideshow video (H.264). Needs the ``[video]`` extra."""
    try:
        import imageio
        import numpy as np
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError('MP4 export needs the video extra: pip install "langstage-codraft[video]"') from exc
    import tempfile
    from pathlib import Path

    from PIL import Image

    imgs = [Image.open(io.BytesIO(p)).convert("RGB") for p in pages]
    if not imgs:
        raise ValueError("no pages to animate")
    w = max(i.width for i in imgs) // 2 * 2
    h = max(i.height for i in imgs) // 2 * 2
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "doc.mp4"
        writer = imageio.get_writer(str(out), fps=fps, codec="libx264", quality=8, macro_block_size=2)
        try:
            for im in imgs:
                canvas = Image.new("RGB", (w, h), "white")
                canvas.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
                arr = np.asarray(canvas)
                for _ in range(max(1, int(seconds_per_page * fps))):
                    writer.append_data(arr)
        finally:
            writer.close()
        return out.read_bytes()
