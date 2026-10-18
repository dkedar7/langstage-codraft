# langstage-codraft: objectives and intentions

This is the yardstick for triaging issues: decide fix, defer or decline against it before
changing code.

## Objective

Give any LangGraph agent a document to co-author. The author drafts **blocks on pages** and owns
the thinking. The agent owns the styling and helps with drafting and research on request. All
agent edits to content are **suggestions the author approves**. Every block has a version tree
and its own sources. Exports (PDF, PNG/JPG, HTML) are pixel-identical to the editor.

## Audience

- **Deployers:** LangGraph developers whose agents produce documents (reports, briefs,
  proposals, papers) and who want a self-hosted, `pip install && run` stage for them.
- **Authors:** whoever uses the deployed app to write.

## In scope

- The paginator: columns, balanced bands, pins as rectangle reservations, repeats, cover and
  back pages, and text splitting. It is pure and unit tested.
- **Editor/export parity.** The pixel-diff gate in `tests/e2e` is load-bearing; never weaken it
  to make a change pass.
- The suggestion/approval flow, comments that tag the agent (handled top to bottom), version
  branches, and per-block references.
- `CODRAFT_TOOLS` and `CodraftMiddleware`, and bringing your own agent as a graph or a factory.
  Human↔agent parity holds because the agent and the UI go through one store/API.
- Deployer themes, templates and config through the LangStage chain (`HostConfig`).

## Anti-scope (decline signals)

- Free-form absolute positioning, or wrapping text around shapes (pins reserve rectangles).
- A general chat or workspace surface. That is `langstage` (web).
- Hosted SaaS or accounts. Live collaboration stays block-level (presence, soft locks, branches on
  conflict); character-level CRDT merging is out unless real demand appears.
- Anything that makes export render differently from the editor.

## Intentions (from the 2026-10-17 scoping)

- 0.2.0 shipped the v2 list: MCP (#45), sketch→image (#46), PPTX/DOCX/GIF/MP4 export (#47),
  block-level live collaboration (#48). The UI follows langstage-web's design system; keep them in step.
- Stay a thin layer over `langstage-core`. If a primitive is needed by two or more stages, it
  belongs in core.
