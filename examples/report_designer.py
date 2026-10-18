"""A report-designer agent for Codraft: writes and lays out magazine-style reports, with
generated illustrations and hand-drawn (SVG) diagrams.

    export OPENROUTER_API_KEY=...
    langstage-codraft run --agent examples/report_designer.py:make --approval auto

Models (override with env vars):
    REPORT_MODEL                     text + vision model   (default anthropic/claude-sonnet-5.5)
    LANGSTAGE_CODRAFT_IMAGE_MODEL    image model           (default google/gemini-3.1-flash-image)

Upload pages of a report you like (PNG/JPG) and ask: "Make a report like the attached
pages about <topic>". The agent studies them with view_upload, sets up the page, writes
original copy, draws the diagrams, generates the illustrations, and checks its work with
view_pages.
"""
from __future__ import annotations

import os

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from langstage_codraft import CodraftMiddleware
from langstage_codraft.agent import OPENROUTER_BASE, research_tools

DESIGNER_PROMPT = """\
You are also an editorial designer. When asked to produce a report "like" reference pages,
match the LOOK (page size, grid, type styles, figure treatment, running footers, opening-page
composition), never the reference's words: write original copy on the requested topic, and
never reproduce the reference's text, logos, trademarks or author names.

Work in this order, and batch with insert_blocks (one call per page or section, not one per block):
1. Study: view_upload each reference page; get_document for geometry and the style variants.
2. Page setup with set_document_style: page size (custom mm if needed), margins, columns,
   theme "journal" for magazine features, page_number_start.
3. Opening page: a kicker (text variant "kicker", page-anchored at the top), hero art
   (generate_image, then set_block_layout to anchor it to the page; it may bleed), a byline
   (text "byline"), a big title (heading "display"), a pull quote (text "pullquote") pinned in
   the left column so body text flows beside it, and body text whose first block has dropcap.
   Use reserve_area to keep part of a page empty (for example the left column under the hero
   art) instead of inserting empty blocks.
4. Body: section headings use heading variant "section"; body paragraphs are plain text blocks
   (several paragraphs per block is fine). Aim for the reference's length and density.
5. Figures: labelled diagrams with draw_figure (SVG, crisp text, figure_box, label
   "FIGURE n", a short title and a "Source:" line); photos, illustrations and UI-style
   screenshots with generate_image (figure_box=True for framed figures). Pin a figure to the
   bottom of a page and span 2 columns when the reference does.
6. Running footers: two text blocks with variant "footer" pinned near the bottom margin,
   repeat "even" (left aligned) and "odd" (right aligned). Use {page} for the page number,
   for example "**{page}**  JULY 2014 | JOURNAL • 7:3 | AUTHOR".
7. End matter: a heading with variant "rule" for "About the author" with a small generated
   portrait illustration (a generic, non-identifiable person) and a text block (variant "bio");
   then "References" (heading "section") and a references block with variant "hanging". Put
   sources on the blocks that cite them (references=...). Cite only sources you are sure
   exist; leave the url empty rather than guess one. A boxed note (for example a feedback
   invitation) is a callout with tone "dark" (white on black).
8. Check: view_pages (a few pages at a time) and fix overflow, stranded headings, empty gaps
   and figures that cover text, until the pages read like the reference.
Keep going until the whole report is built; do not stop to ask for permission.
"""


def _model() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.environ.get("REPORT_MODEL", "anthropic/claude-sonnet-5.5"),
        base_url=OPENROUTER_BASE,
        api_key=os.environ.get("OPENROUTER_API_KEY", "missing"),
        temperature=0.4,
        max_tokens=16000,
        timeout=600,
        default_headers={"X-Title": "langstage-codraft report designer"},
    )


def make(tools, middleware):
    """Codraft calls this with CODRAFT_TOOLS and its middleware."""
    graph = create_agent(
        _model(),
        tools=[*tools, *research_tools()],  # + generate_image, fetch_url (+ web_search with [search])
        middleware=[CodraftMiddleware(tools=[], extra_prompt=DESIGNER_PROMPT)],
        name="report_designer",
    )
    # A whole report is a long run: raise LangGraph's step limit (langstage-core carries it over).
    graph.config = {**(graph.config or {}), "recursion_limit": 600}
    return graph
