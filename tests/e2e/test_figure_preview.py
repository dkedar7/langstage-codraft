"""The agent's view of a figure it drew, in a real browser.

``draw_figure`` comes back with a screenshot of the figure as it sits on the page (the
same print view as export) and a label check measured on the rendered SVG. Found by the
2026-11 re-evaluation, where both drawn diagrams shipped with labels crossed by arrows,
spilling out of their boxes and under oversized arrowheads.
"""
from __future__ import annotations

import io
import tempfile
from pathlib import Path

from conftest import api  # noqa: E402
from PIL import Image

from langstage_codraft import render
from langstage_codraft.render import Renderer
from langstage_codraft.store import get_store
from langstage_codraft.tools import draw_figure, sanitize_svg

CLEAN = """<svg viewBox="0 0 600 240" xmlns="http://www.w3.org/2000/svg">
<rect x="20" y="80" width="180" height="80" fill="#e8f0fa" stroke="#2d6cdf"/>
<text x="110" y="126" font-size="22" text-anchor="middle" font-family="Arial">Sun</text>
<rect x="400" y="80" width="180" height="80" fill="#e8f0fa" stroke="#2d6cdf"/>
<text x="490" y="126" font-size="22" text-anchor="middle" font-family="Arial">Roof</text>
<line x1="200" y1="120" x2="396" y2="120" stroke="#2d6cdf" stroke-width="3"/>
<text x="300" y="104" font-size="20" text-anchor="middle" font-family="Arial">heat</text>
</svg>"""

# One of each problem: a label on its arrow, a label wider than its box, a label past the
# edge, two labels on top of each other, and type that prints under 6.5 px in a column.
BROKEN = """<svg viewBox="0 0 600 240" xmlns="http://www.w3.org/2000/svg">
<rect x="20" y="80" width="120" height="60" fill="#e8f0fa" stroke="#2d6cdf"/>
<text x="80" y="118" font-size="22" text-anchor="middle" font-family="Arial">Incoming shortwave radiation</text>
<line x1="140" y1="200" x2="560" y2="200" stroke="#2d6cdf" stroke-width="3"/>
<text x="350" y="207" font-size="20" text-anchor="middle" font-family="Arial">crossed label</text>
<text x="520" y="40" font-size="22" font-family="Arial">runs off the edge</text>
<text x="300" y="60" font-size="20" font-family="Arial">first</text>
<text x="305" y="62" font-size="20" font-family="Arial">second</text>
<text x="30" y="230" font-size="9" font-family="Arial">tiny note</text>
</svg>"""


def test_the_label_check_finds_each_kind_of_problem_and_passes_a_clean_figure(server):
    with tempfile.TemporaryDirectory() as td:
        r = Renderer(server, Path(td))
        try:
            assert r.call(r.svg_issues(CLEAN, 330)) == []
            found = "\n".join(r.call(r.svg_issues(BROKEN, 330)))
            assert 'A line (#2d6cdf, from (140, 200) to (560, 200)) runs through "crossed label" (x ' in found
            assert '"Incoming shortwave radiation" is wider than the box it sits in' in found
            assert '"runs off the edge" is cut off at the right edge' in found
            assert '"first" (x ' in found and 'and "second" (x ' in found and "overlap each other" in found
            assert "print smaller than 6.5 px" in found and '"tiny note"' in found
            # the same figure at page width: only the type-size complaint goes away
            wide = "\n".join(r.call(r.svg_issues(BROKEN, 700)))
            assert "print smaller" not in wide and "runs through" in wide
        finally:
            r.close()


def test_an_arrowhead_over_a_label_is_reported_and_tamed_markers_fit(server):
    arrow = ("<svg viewBox='0 0 600 200' xmlns='http://www.w3.org/2000/svg'><defs><marker id='a' markerWidth='11' "
             "markerHeight='11' refX='8' refY='5.5' orient='auto'><path d='M0,0 L11,5.5 L0,11 z' fill='#2d6cdf'/>"
             "</marker></defs><path d='M40 100 L300 100' stroke='#2d6cdf' stroke-width='10' fill='none' "
             "marker-end='url(#a)'/><text x='310' y='70' font-size='22' font-family='Arial'>Indoor coil</text></svg>")
    with tempfile.TemporaryDirectory() as td:
        r = Renderer(server, Path(td))
        try:
            raw = "\n".join(r.call(r.svg_issues(arrow, 600)))
            assert "An arrowhead (at (300, 100), on the #2d6cdf line from (40, 100)) covers part of" in raw
            assert '"Indoor coil" (x 310-' in raw  # 110 units of arrowhead reach the label
            assert r.call(r.svg_issues(sanitize_svg(arrow), 600)) == []  # 30 units once tamed
        finally:
            r.close()


def test_an_arrow_drawn_along_another_arrow_is_reported(server):
    """The live re-run: the upward heat arrow was drawn on top of the incoming-sun arrow."""
    stacked = ("<svg viewBox='0 0 400 300' xmlns='http://www.w3.org/2000/svg'>"
               "<line x1='200' y1='40' x2='200' y2='220' stroke='#f2b705' stroke-width='12'/>"
               "<line x1='200' y1='220' x2='200' y2='120' stroke='#c74a2c' stroke-width='8'/></svg>")
    apart = stacked.replace("x1='200' y1='220' x2='200'", "x1='240' y1='220' x2='240'")
    crossing = ("<svg viewBox='0 0 400 300' xmlns='http://www.w3.org/2000/svg'>"
                "<line x1='40' y1='40' x2='360' y2='260' stroke='#333' stroke-width='4'/>"
                "<line x1='40' y1='260' x2='360' y2='40' stroke='#333' stroke-width='4'/></svg>")
    with tempfile.TemporaryDirectory() as td:
        r = Renderer(server, Path(td))
        try:
            found = " ".join(r.call(r.svg_issues(stacked, 400)))
            assert "Two lines are drawn on top of each other" in found
            assert "(#f2b705, from (200, 40) to (200, 220))" in found and "(#c74a2c, from (200, 220) to (200, 120))" in found
            assert r.call(r.svg_issues(apart, 400)) == []
            assert r.call(r.svg_issues(crossing, 400)) == []  # lines that merely cross are fine
        finally:
            r.close()


def test_draw_figure_returns_the_figure_as_it_sits_on_the_page(server, monkeypatch):
    c = api(server)
    did = c.post("/api/docs", json={"title": "Figures"}).json()["id"]
    c.post(f"/api/docs/{did}/blocks", json={"type": "text", "content": {"markdown": "Before the figure."}})
    cfg = {"configurable": {"thread_id": did}}
    with tempfile.TemporaryDirectory() as td:
        r = Renderer(server, Path(td))
        monkeypatch.setattr(render, "ensure_renderer", lambda: r)
        try:
            # approval required: the figure is a pending insert, previewed through ?preview=pending
            text, art = draw_figure.func(svg=BROKEN, title="Energy flows", caption="A caption.", config=cfg)
            (sg,) = [s for s in get_store().list_suggestions(did, status="pending")]
            assert f"The figure's id is {sg['id']}" in text and "PREVIEW attached" in text and "page 1" in text
            assert "LABEL CHECK found" in text and 'runs through "crossed label"' in text
            im = Image.open(io.BytesIO(Path(art["images"][0]).read_bytes()))
            assert im.width > 600 and im.height > 250  # the block at 2x: frame, title and caption included
            assert len(set(im.convert("L").resize((40, 20)).getdata())) > 3  # not a blank frame
            # the author's document is untouched until they accept
            assert [b["type"] for b in c.get(f"/api/docs/{did}").json()["blocks"]] == ["text"]
            # redraw the pending figure, clean this time
            text, art = draw_figure.func(svg=CLEAN, block_id=sg["id"], title="Energy flows", config=cfg)
            assert "LABEL CHECK: no cut-off" in text and art["images"]
            # accepted, then redrawn in auto mode: the preview is the block itself
            get_store().accept_all(did)
            c.patch(f"/api/docs/{did}", json={"settings": {"approval": "auto"}})
            fig = next(b for b in get_store().list_blocks(did) if b["type"] == "image")
            text, art = draw_figure.func(svg=CLEAN.replace("heat", "warmth"), block_id=fig["id"], config=cfg)
            assert f"The figure's id is {fig['id']}" in text and "PREVIEW attached" in text and art["images"]
        finally:
            r.close()
