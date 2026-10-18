---
title: langstage-codraft — requirements (v1 "Paged core")
date: 2026-10-17
category: human-and-agent
backlog_id: 44
status: approved (Kedar, 2026-10-17 — v1 scope "Paged core")
source: docs/notes/page-1..5.jpg (Kedar's handwritten design notes)
---

# langstage-codraft — the document stage for your LangGraph agent

## Summary

**Category: human-and-agent.** A self-hosted, `pip install`-able web app in the LangStage
family where an author drafts a document as **blocks on pages** and a LangGraph agent
co-authors it. The split of control is the product's thesis:

- **The author owns the thinking**: ideas, structure, and final say over every word.
- **The agent owns the styling**: layout, theme, fitting content to the space. It also
  helps on request with drafting, research (an uploaded-document index, web search, or any
  tool the deployer gives it), and revisions.

The agent edits only through **suggestions the author approves**; a global setting can
turn approval off. Every block keeps a **version tree**, so nothing the agent does is
irreversible. The agent can **look at the rendered pages** to judge layout the way a
human does. Documents export to **PDF, HTML (zip) and PNG/JPG**, and every export is
produced from the same renderer as the on-screen preview.

```
pip install langstage-codraft
langstage-codraft run --agent my_agent.py:graph   # bring your own LangGraph agent
langstage-codraft run --demo                      # keyless scripted demo
```

## Problem frame

All five LangStage surfaces (web, CLI, Jupyter, VS Code, Hermes) are shaped around chat
with the agent. None of them produces a finished artifact that people author together.
Teams that already have a LangGraph agent — a research agent, a report generator, an
internal-knowledge assistant — have no self-hosted way to put it inside a document the
author owns. The hosted incumbents (Notion AI, Docs+Gemini, Copilot Pages, Gamma) don't
accept your agent. LangChain's own Open Canvas, the closest OSS precedent, is archived
(Feb 2026), runs its own fixed agents, and has no page layout or export.

### Kill-test findings that shape the positioning (2026-10-17)

| Axis | Verdict | What we take from it |
|---|---|---|
| already-solved | survive 0.70 | No self-hosted, bring-your-own-LangGraph, paged co-authoring app exists. Don't build a second `langstage` canvas: this product is author-owned. |
| infeasible | survive 0.72 | General CSS reflow (Paged.js, CSS multicol, wrapping text around floats) would be fatal. Use a **custom block paginator**, treat pinned blocks as **reserved rectangles**, and get **parity by construction** (export prints the same React renderer in Chromium). Playwright is an optional extra. |
| pain-is-rare | refute 0.60 | We have no demand signal for the narrow niche, so v1 must be good enough on its own to create demand. |
| won't-be-adopted | refute 0.75 | Wedge: **LangGraph developers whose agents produce documents** (reports, briefs, proposals). Headline: *"the agent sees the rendered page."* Get distribution through the docs site and the AG-UI/LangGraph community, not through LangStage's own audience. |

Both refutes are about distribution and demand, not about whether the product can or
should exist. The owner decided to build it; these findings set the positioning and the
v1 cut.

## Users

- **Deployers** (developer or platform team): install the app, point it at a LangGraph
  graph, and set themes, templates and policy.
- **Authors** (users of the deployed app): academics, industry professionals writing
  design documents and proposals, marketers writing copy, designers, scientists, business
  leaders writing memos, students.

## Requirements

### Interface contract (mandatory for human-and-agent)

- **R1. Human-first interface.** The primary entry point is a web UI for authors, plus a
  `langstage-codraft` CLI for deployers and scripting (`run`, `check`, `new`, `list`,
  `export`, `install-browser`, `--show-config`). The agent path is secondary.
- **R2. Human↔agent parity.** Anything the agent can do to a document, a human can do in
  the UI, and vice versa. This covers reading, inserting, editing, moving and deleting
  blocks, changing document settings, resolving comments, viewing rendered pages, and
  exporting. Both go through the same HTTP API and store. The agent has no private path,
  and the human is not locked out of any operation.

### Document model

- **R3. A document is an ordered list of typed blocks plus settings.** Block types in v1:
  `text` (rich text), `heading`, `image`, `table`, `chart` (a Vega-Lite spec), `callout`,
  `divider`, `references`, and `page-break`.
- **R4. The seven creation inputs** (notes p.1) are stored on the document and shown to the
  agent:
  1. layout (paged or pageless, page size, orientation, margins, columns 1–3, inter-block
     margin, theme)
  2. writing style
  3. objective
  4. platform
  5. audience
  6. language
  7. output format

  These are set through a new-document form where the author picks options or types
  natural language. Every field is optional and can be edited later.
- **R5. Per-block version tree.**
  - Every content change creates a version, whether a human edit or an accepted agent
    suggestion.
  - Moving back to an earlier version and then editing starts a new **branch**; nothing is
    overwritten.
  - Authors and agent can step back and forward and switch branches.
- **R6. Per-block references.** Each block keeps a list of sources (title, url, note). The
  agent produces the document's full reference list on request, and a `references` block
  renders it.
- **R7. Uploads.** Authors upload files (PDF, DOCX, MD, TXT, HTML, CSV) to a document,
  either as **reference material** (indexed so the agent can search it) or as a
  **template** (the agent uses its structure to scaffold the new document).

### Layout (paged mode)

- **R8. Pages.**
  - Page sizes: Letter, A4, Legal, A5, 16:9 slide.
  - Settings: orientation and margins.
  - **Add page** appends a blank page of the selected size; content beyond the last page
    adds pages automatically.
- **R9. Columns.** The document has a 1-, 2- or 3-column setting that applies to every
  page. A block can span multiple columns.
- **R10. Flow layout.**
  - Blocks flow in order through the columns and pages.
  - The inter-block margin is respected.
  - When a block grows, the blocks after it move down.
  - Text blocks split across columns and pages at paragraph boundaries. Other blocks
    never split; one that doesn't fit moves to the next column.
- **R11. Pinned blocks.** A block can be pinned to a page at a vertical position, a column
  and a span. A pinned block reserves that space, and flowing blocks fill the space around
  it. Pins are deterministic rectangle reservations, not text wrapping.
- **R12. Repeat across pages.** A pinned block can repeat on all pages, a list of pages, or
  odd/even pages (for headers, footers and logos).
- **R13. Cover and back pages.** Blocks can be assigned to a cover page or a back page.
  These pages are exempt from the column rule and from repeated blocks, and their content
  is centred on a single column.
- **R14. Direct manipulation.**
  - Dragging a block reorders it, with drop indicators that snap to positions in the flow.
  - Dragging the side edge changes the column span, snapping to column edges. Dragging the
    bottom edge sets a fixed height, snapping to the baseline grid; clearing it returns the
    block to auto height.
  - Any block can be duplicated, or replicated to all or selected pages.
- **R15. Pageless mode** uses the same blocks in one continuous column with no page
  furniture.
- **R16. Themes.**
  - Built-in themes: `classic`, `modern`, `academic`, `minimal`.
  - Deployers add more in TOML: fonts, sizes, colours, heading scale, accent colour.
  - Inserted blocks pick up the theme, so the document stays visually consistent.

### Agent collaboration

- **R17. Comments tag the agent.**
  - Any block can carry comments.
  - A comment can be **queued** for later or **sent now**, which starts an agent run
    scoped to that comment.
  - "Address all comments" handles open comments **strictly top to bottom** in document
    order.
- **R18. Approval gate.**
  - Agent writes arrive as **suggestions**, shown as a diff on the block with Accept and
    Reject.
  - A document-level setting, with a deployer default, enables auto-apply. Auto-applied
    edits still create versions, so they can be undone.
- **R19. Continuity suggestion after a move.** When a block is moved, the UI offers
  "rewrite affected blocks for continuity". This queues a comment for the agent on the
  neighbouring blocks.
- **R20. Context discipline.** The agent sees the document through tools, not as one large
  context dump. The tools cover:
  - the outline and settings
  - reading specific blocks
  - searching the document and uploads
  - listing comments with their history
  - reading block versions, so it can respect preferences stated in past comments
- **R21. Visual view.** The agent has a `view_pages` tool that returns page screenshots
  from the same renderer, so a vision-capable model can judge layout.
- **R22. Fit to the block.** When a block has a fixed height, the agent is told its
  approximate word budget and aims to stay within it. This is best effort, not exact,
  unless the author asks for more words.
- **R23. Missing tool.** If a block needs a capability the agent's tools don't provide, the
  agent says so plainly (on the block or in chat) instead of failing silently.
- **R24. Bring your own agent.**
  - The deployer passes a compiled LangGraph graph (`--agent path.py:graph`). Codraft adds
    `CODRAFT_TOOLS` and `CodraftMiddleware`, in the same way `langstage` adds
    `display_inline`.
  - A deployer-built graph can include the tools explicitly.
  - Without `--agent`, a default agent is built from the configured model (any
    LangChain/OpenRouter model).
  - `--demo` runs a keyless scripted agent that exercises the full suggestion flow.
- **R25. Chat panel** for document-level requests ("address all comments", "tighten
  section 2", "find three sources on X").
- **R26. One agent thread per document.**
  - The conversation persists with the document; the AG-UI thread id is the document id.
  - Several documents can be open in tabs, and a history sidebar lists all documents.

### Deployer journey

- **R27. Configuration** follows the family chain (defaults < `langstage.toml` <
  `LANGSTAGE_*` env < CLI), via a `HostConfig` subclass. The `[codraft]` table holds:
  - the model
  - the approval default
  - allowed and default themes
  - the templates directory
  - the data directory
  - the export defaults
- **R28. Templates** are JSON or TOML files that set preset settings and a block skeleton.
  They are listed in the new-document form.
- **R29. `check`** preflight: loads the agent, reports whether the tools are present, the
  checkpointer, the browser for export and vision, and model reachability. `--live` runs
  one real turn through `langstage_core.agui.verify`.

### Output

- **R30. Preview.** A print-faithful preview renders exactly what will be exported.
- **R31. Export:**
  - **PDF**: vector, one PDF page per document page, page size taken from the settings.
  - **HTML**: a zip with `index.html`, CSS and assets. Interactive content such as charts
    stays live.
  - **PNG/JPG**: one image per page, zipped when there are several.

  All three are produced by headless Chromium (the `[export]` extra) from the same renderer
  as the preview. Without Chromium, the browser's own print-to-PDF is offered instead.

## Scope boundaries

**Deferred to v2 (owner decision):**
- Sketch-to-image on image blocks
- Video and GIF blocks
- PPTX, DOCX, MP4 and GIF export
- Real-time multi-user editing
- Wrapping text around shapes (beyond pinned rectangle reservations)
- Merging branches

**Out of scope:** hosted SaaS, user accounts or authentication beyond the family's
loopback/token defaults, and a mobile UI.

## Success criteria

1. `pip install langstage-codraft && langstage-codraft run --demo` opens the app in a
   fresh environment with no API key. The author can create a document, add blocks,
   comment, ask the demo agent, accept or reject its suggestion, step a block back to an
   earlier version, and export a PDF.
2. **Parity gate:** a CI visual test confirms that a page in the editor preview and the
   same page in the exported PNG match within tolerance.
3. With a real model (OpenRouter), "address all comments" on a three-comment document
   handles the comments top to bottom, proposes suggestions with references, and calls
   `view_pages` at least once when asked to fix layout.
4. Bringing your own agent works: `--agent` with a plain `create_agent` graph, without the
   codraft tools, still works because the tools are injected. `check` reports this
   accurately.
5. Released on PyPI with CI covering Python 3.11–3.14, a frontend build, e2e tests and
   visual tests; a page added to the LangStage docs site; acceptance tested with
   pm-dogfood.

## Addendum, 2026-10-18: 0.2.0

The scope boundaries above moved: Kedar asked for the deferred items to be addressed and for the UI
to match langstage-web.

- **R32. MCP surface.** CODRAFT_TOOLS are served over MCP (HTTP at `/mcp`, stdio via
  `langstage-codraft mcp`) with an explicit `doc_id`; content edits stay approval-gated.
- **R33. Sketch to image.** Image blocks carry an optional sketch the agent can view; a default
  `generate_image` tool turns it into an image suggestion.
- **R34. More output.** DOCX (reflowing), PPTX (positioned, editable text), GIF and MP4 slideshows;
  video/GIF blocks print their poster frame.
- **R35. Live collaboration, block-level.** Server-pushed changes, presence, soft locks, and
  concurrent edits preserved as branches. Character-level co-editing (CRDT) stays out of scope.
- **R36. Family design.** The app chrome uses langstage-web's tokens and components, light and dark.
