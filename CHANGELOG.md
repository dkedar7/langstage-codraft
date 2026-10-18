# Changelog

## 0.3.0 — 2026-10-18

Magazine-quality layout, and an agent that designs reports from reference pages.

- **Page setup:** custom page sizes (`page_w_mm`/`page_h_mm`), `page_number_start`, and `{page}`,
  `{pages}`, `{title}` tokens in running headers and footers.
- **Typography:** a `journal` theme (justified, hyphenated, indented paragraphs); block styles
  (`lead`, `pullquote`, `kicker`, `byline`, `small`, `caption`, `footer`, `bio`; headings `display`,
  `section`, `rule`, `label`; `figure-box`; `hanging` references; `dark` callouts) and drop caps.
- **Placement:** page-anchored pins that can bleed past the margins, and reserved areas
  (`spacer` blocks, `reserve_area`) that keep part of a page empty.
- **Figures:** a label / title / source frame; reference lists split across columns and pages.
- **Agent tools:** `view_upload` (see reference pages), `draw_figure` (sanitized SVG diagrams),
  `insert_blocks` (batch), `reserve_area`; `generate_image` can create new figures with an aspect
  ratio. Up to 8 tool images reach the model per call.
- **Example:** `examples/report_designer.py`, an OpenRouter agent (Claude Sonnet 5.5 + Gemini image
  model) that writes and lays out a report like uploaded reference pages.
- **Security:** uploaded files are served with a strict content security policy (SVG can carry script).

## 0.2.0 — 2026-10-18

The remaining backlog items from the 0.1 scoping (#45–#49) plus a redesign.

- **langstage-web's design system.** Same tokens, header, pill tabs, chat messages, tool-call cards,
  composer and light/dark/auto toggle (`[ui] theme`, `LANGSTAGE_THEME`). The canvas and side panel
  are resizable, and zoom follows the canvas width until you pick one.
- **MCP** (#45): Codraft's tools at `/mcp` (streamable HTTP) and `langstage-codraft mcp` (stdio),
  including `view_pages` and `view_sketch` images. Edits stay approval-gated.
- **Sketch to image** (#46): a sketch pad on image blocks, the `view_sketch` tool, and a
  `generate_image` tool on the default agent (OpenRouter image model). Images from any tool reach
  vision models.
- **More output** (#47): Word (`.docx`), PowerPoint (`.pptx`), animated GIF and MP4 (`[video]` extra)
  export, and video/GIF blocks with poster frames for print.
- **Live collaboration** (#48): changes pushed over server-sent events, presence (avatars and named
  outlines on the blocks people have selected), a soft lock on blocks being edited, and concurrent
  edits that become version branches instead of overwriting.
- **Papercuts** (#49): `/agui` answers 404 for an unknown document instead of running with failing
  tools, and the sidebar's block counts stay fresh.
- Fixed: a default agent with an OpenRouter key could fail to build (an unresolvable type hint on a tool).

## 0.1.1 — 2026-10-18

Fixes from the pm-dogfood acceptance pass on 0.1.0:

- The README's bring-your-own-agent example now runs as written; it no longer uses undefined names.
- Added an `[anthropic]` extra, and a model whose provider package is missing now names the package to install.
- `--show-config` no longer lists the documented `codraft.themes.*` and `codraft.allowed_themes` keys as unknown.
- Accepting the last suggestion tied to a comment resolves that comment.
- When a template has a title heading, it takes the new document's title.
- New blocks go before a trailing References block when nothing is selected.
- Pressing Escape on an untouched new text block removes it. Escape also closes Preview.
- `export` gained `--port`, gives a one-line error when no server is running, and adds a `.zip` suffix to zipped exports.
- `install-browser` confirms success.
- The demo agent can shorten single-sentence blocks, and its drafts read better.
- The PyPI Documentation URL now points at `stages/codraft/`.

## 0.1.0 — 2026-10-18

The first release ("Paged core").

- Block editor with paged and pageless documents. Paged documents support page sizes, orientation, margins, 1–3 columns, pinned and repeating blocks, and cover and back pages.
- Block types: text, heading, image, table, Vega-Lite chart, callout, divider, references, and page break.
- Agent collaboration:
  - Edits arrive as suggestions with diffs, behind an approval gate with an optional auto-apply.
  - Comments tag the agent; "address all comments" handles them top to bottom.
  - Each block keeps a version tree with branches and a list of sources.
  - `view_pages` gives vision models screenshots of the rendered pages.
- `CODRAFT_TOOLS` (15 tools) and `CodraftMiddleware`. Agents can be brought in as a graph or a factory. Includes a default agent and a keyless `--demo` agent.
- Export to PDF, PNG/JPG, and an HTML zip, rendered by the same React renderer as the editor.
- The new-document form takes the seven brief inputs, plus uploads used as references or templates.
- Deployers can set themes, templates and a default approval mode in `langstage.toml`.
- CLI commands: `run`, `check [--live]`, `new`, `list`, `export`, `install-browser`.
