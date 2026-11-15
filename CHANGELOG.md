# Changelog

## 0.10.0 — 2026-11-15

**The look has a history, so a restyle can be undone (#54).** The agent changes the look without
approval, by design, and until now nothing recorded it: content had a version tree per block,
the look had only "Reset to Classic".

- **Design history.** Every change to the document settings, typography, cover, header and
  footer, block layouts and reserved areas is a step, with who made it (the author or the
  agent), when, and a one-line summary ("1 → 2 columns, body font Lato, 14 block layouts").
  - One agent turn is one step, however many styling calls it makes. An author's changes within
    ten seconds are one step (a slider, a colour picker). An MCP client's calls are grouped by
    time.
  - **Undo** reverts one step and leaves later changes in place; what was restyled again since
    keeps its newer look. **Back to before this** restores the look from before a step, undoing
    it and everything after it.
  - A restore is itself a step, so it can be undone. It changes settings, layouts and reserved
    areas only: it never edits, creates or deletes a content block. Layouts of blocks deleted
    since are skipped; blocks added since are left alone.
  - The newest 200 steps of each document are kept.
- **Editor.** Design → **History** lists the steps with **Undo** and **Back to before this**, and
  **Undo the agent's last restyle** at the top. After a turn in which the agent changed the
  look, a notice says what changed and offers **Undo**.
- **API.** `GET /api/docs/{id}/design-history` and
  `POST /api/docs/{id}/design-history/{step}/restore` (`{"only": true}` undoes that step alone).
  `Store.design_history`, `Store.restore_design`, `Store.last_agent_design`. `update_document`,
  `set_layout` and `reserve_area` take `by` and `group`.
- **Agent and MCP.** New tools `list_design_history` and `restore_design`: `CODRAFT_TOOLS` has
  29 tools. The agent is told to go back only when the author asks, and its next turn is told
  when the author undid its styling or changed the look themselves.
- **A chart's problems come back with the tool result.** `propose_insert`, `propose_edit` and
  `insert_blocks` end with `CHART CHECK:` when a chart puts different units on one axis or uses
  colours too close to tell apart (`charts.chart_warnings`, added in 0.9.0, was not wired in).
- **Prompt.** The co-author's prompt now names `anchor="bottom"` for a block at the foot of a
  page and back-fill for a gap before a figure; before, only the tool descriptions did.

## 0.9.1 — 2026-11-15

- **Fixed: the agent could shorten the exported document without approval (#58).**
  `set_block_layout(height_px=…)` is styling, so it applied at once, and a small height on one
  of the author's text blocks left most of that block out of every export, with no suggestion,
  no version and no warning. The tool now measures the block in the print view and refuses a
  height, or a pin's box, that would cut off a text, callout, heading, table, row, tiles or
  references block. The refusal names the height the content needs, nothing is changed, and it
  points to `propose_edit` for making the text shorter. This holds in every approval mode and
  for MCP clients and bring-your-own agents, which never saw the built-in prompt's rule.
  - A restyle of a block the author had already boxed is still allowed; making that box
    smaller, or a restyle that makes fitting content outgrow its box, is not.
  - Without a renderer (no Chromium) the tool refuses only a box plainly too small for the
    block's word count.
  - `set_typography`, `set_document_style` and `match_upload_style` end with an `OVERFLOW:`
    line when they leave a fixed-height block cut off.
  - A pending insert keeps its layout inside the suggestion, as before: the author sees it.
- **An export says what it cut off.** A block that is cut off on the page is now reported by
  every way to export:
  - `langstage-codraft export` prints a `[warn]` line naming the blocks and pages. `--strict`
    fails that document instead (exit 1, nothing written); `--json` prints one object per
    export with `file`, `bytes` and `cut_off`.
  - `GET /api/docs/{id}/export` sets `X-Codraft-Cut-Off: blk_…@page,…`; `?strict=true` answers
    409 with the list. `GET /api/docs/{id}/layout-check` returns the list on its own, with the
    height the content needs and the height it has.
  - `export_document` and `Standalone.export` emit a `CutOffWarning`; `strict=True` raises
    `ExportError` (status 409). `Standalone.check(doc_id)` returns the list.
  - A Word export has no warning: Word reflows the text.
- **The editor tells the author.** The Block panel shows how much of a fixed-height block is
  cut off, with **Fit the content** and **Auto height**; the Export button's notice names the
  page and opens the block. Authors can still set any height: it is their text.
- `Store.set_layout(block_id, layout, replace=True)` sets a layout exactly, without merging.

## 0.9.0 — 2026-11-14

**Pages without holes, tables that split, references with authors.** From a second real-model
evaluation, of 0.8.0: the documents were sound but none could be sent as exported, mostly
because of half-empty pages, plain-text tables and a reference list that printed working notes.

- **A long table continues on the next page.** A table block splits between rows, across
  columns and pages, and every continued piece repeats the header row. A split keeps at least
  two rows on each side, the caption stays with the last rows, and a table with a fixed height
  stays one box. A Markdown table inside a text block splits between rows too. Before, a table
  that didn't fit moved whole and left up to half a page empty. Word export marks the header
  row to repeat.
- **Text fills the gap before a figure (back-fill).** A chart, picture, video, row or set of
  tiles that doesn't fit at the bottom of a page lets the text after it, up to the next
  heading, fill the gap, and opens the next column or page itself. One figure steps aside at a
  time, so figures keep their order, and a heading stays with the text it introduces.
  `settings.backfill` (Document → *Let text fill the gap before a figure*,
  `set_document_style(backfill=...)`). **On for new documents; a document saved before 0.9.0
  keeps its pages as they were** until the author or the agent turns it on.
- **Stick to the bottom of the page.** `layout.anchor: "bottom"` (Block panel → *Stick to the
  bottom of the page*, `set_block_layout(anchor="bottom")`) puts a flowing block at the foot of
  the page the flow has reached; later blocks flow above it, and it moves to the foot of the
  next page when it doesn't fit. A call to action or a closing band no longer needs a pin and
  a reserved area. The one-pager template's call to action uses it.
- **Fixed: a pinned band drew at its own height at the top of its box.** A filled heading or
  callout with a fixed height now fills the box, with its text in the middle.
- **Cut-off content is reported.** A pinned or fixed-height block whose content is taller than
  its box was clipped silently in the export. The editor flags it (now for headings, tables and
  reference lists too), and `view_pages` tells the agent `OVERFLOW: blk_… on page N`. A
  page-anchored pin that bleeds into the margin is no longer flagged; one that runs past the
  page edge is.
- **`reserve_area` replaces what it overlaps.** Reserving a rectangle removes the reserved
  areas it overlaps on that page, so repeated calls move or resize the area instead of stacking
  spacers. `remove=True` only removes them. `Store.reserve_area`.
- **Table cells are Markdown.** A cell of a table block takes `**bold**`, `*italic*`,
  `` `code` ``, links, `m^2^`, `CO~2~` and citations, in the editor and every export.
  `"total": true` sets the last row as a total (Block panel → *Last row is a total*).
- **References have authors and a year, and the note is private.** A source keeps `authors`,
  `year`, `publisher` and `accessed` beside its title and URL, and the list prints
  `Authors (Year). Title. Publisher. URL. Accessed DATE.` in the editor, PDF, HTML, PNG, Word
  and PowerPoint. The Block panel edits the fields. **Changed: `note` is no longer printed.**
  It is a private note for the author and the agent; before, it was printed after the title.
- **Citations.** `[@key]` in a text block, callout, row cell or table cell prints as the
  source's number, linked to its entry (`[@a; @b]` prints `[1, 2]`). Every source has a `key`
  (its own, or one made from the first author and year) and numbers follow first appearance:
  where the text first cites a source, else where its first block stands. Sources saved by an
  older version read back with a key and need no migration. `compile_references` and
  `read_blocks` show the key to cite by. New module `langstage_codraft.refs`.
- **Import cites by key.** A footnote marker becomes `[@key]` (it was a typed-in `[1]`), and a
  footnote shaped like `Rivera, A. (2024). Title. Publisher.` is read into authors, year, title
  and publisher.
- **Charts.** A stacked bar or area chart now stacks in the order its legend lists (a
  `color.sort` list was ignored by the stack, and a horizontal stack ran against its legend).
  Value labels after a bar's end or above it get room and are no longer cut off at the chart's
  edge. `langstage_codraft.charts.chart_warnings(spec)` reports one axis that mixes units and
  colours that are hard to tell apart.
- **Fixed: a click on a link in the editor left the page.** It selects the block; links are
  live in the preview and the exports.

**The co-author's side of the 0.8.0 re-evaluation.** A strong model wrote the same four
documents again on 0.8.0. These are the agent and tool problems it still had.

- **The agent sees the figure it drew.** `draw_figure` returns a screenshot of the figure as it
  sits on the page (the block from the same print view as export, at twice its size) and a
  label check measured on the rendered SVG at its printed size: labels cut off by the canvas,
  type that prints under 6.5 px, labels on top of each other, a label wider than the box it
  sits in, a line or an arrowhead through a label, and one arrow drawn along another. Each
  finding names the label and the line with their positions in viewBox units. Over MCP the tool
  returns the image too. New: `Renderer.figure_shot` and `Renderer.svg_issues`.
- **Fixed: giant arrowheads.** An SVG marker is sized in multiples of the stroke width unless it
  says otherwise, so a thick connector (stroke-width 10) with `markerWidth="11"` drew a
  110-unit arrowhead over its labels. A marker more than four times as wide as the thickest
  line using it is rewritten to user units at three times that line's width when the figure
  is stored.
- **Redraws are capped.** One figure can be drawn five times in a turn. From the third version
  the agent is told to simplify instead of nudging coordinates; a sixth is refused and the
  agent tells you what is still wrong.
- **Sources are not cut to make a page fit.** Deleting the reference list, or a block that
  cites a source, waits for your approval in every approval mode, `auto` included
  (`Store.propose(..., hold=True)`). The prompt rule against deleting or shortening content
  for layout now covers the agent's own content and every mode.
- **Web search is part of the default install** (`ddgs`, one binary wheel beyond what was
  already required; the `[search]` extra still installs). When it can't be imported,
  `langstage-codraft check` says so and the agent is told it has no search and must not guess
  URLs. `web_search` reports "no results" as an answer, not as an outage, and says whether the
  service rate-limited, timed out or could not be reached.
- **Guessed URLs stop being opened.** After six failed fetches in a turn `fetch_url` only opens
  URLs that `web_search` returned, and 24 fetches is the limit for a turn. A failed fetch says
  what the status means in one line. `fetch_url` returns 4,000 characters by default (was
  6,000); `offset` still pages.
- **`generate_image` is offered only where it can work**: `OPENROUTER_API_KEY` is set and
  either the model is an `openrouter:` one or `LANGSTAGE_CODRAFT_IMAGE_MODEL` is set. An
  OpenRouter key that sits in the environment beside another provider's model no longer gets
  the tool. **Changed:** `research_tools()` takes the model spec (`research_tools(spec)`);
  called with none, it leaves `generate_image` out unless `LANGSTAGE_CODRAFT_IMAGE_MODEL` is
  set. A 401 or 402 from the image service comes back as one plain line.
- **Word counts where a length target needs them.** `get_document` gives the total,
  `read_blocks` each block's count (and the accepted and proposed counts of a pending edit),
  and `propose_edit` the words before and after with the change in percent. The agent is told
  to work out the target number and check against it before it reports a length as met.
- **One block per paragraph, enforced.** A text draft of two or more paragraphs and more than
  150 words is split into one block per paragraph, in order, by `propose_edit` (when the
  block held a single paragraph), `propose_insert` and `insert_blocks`. Lists, tables, quotes
  and code stay with their paragraph. `keep_together` keeps a draft whole; a block you already
  keep as several paragraphs, a fixed-height block and a styled one are never split.
- **Fewer needless history lookups.** `read_blocks` marks a block HISTORY when you revised it
  or resolved a comment on it; `get_block_history` answers in one line for any other block.
- **Less sent to the model.** Tool results from earlier turns are sent cut down in later
  turns: a fetched page keeps its opening lines, an old outline or block read is replaced by
  a note to read it again. The thread keeps them whole. Replaying the evaluation's threads,
  this is 40% less message text on the two long documents and 36% over all five.
  `CodraftMiddleware(trim_history=False)` turns it off. `view_pages` no longer attaches a page
  that renders exactly as it did when the agent last looked in the same turn.
- `langstage-codraft check` gains `search` and `images` lines for the built-in agent, and
  `/api/config`'s agent info gains `web_search` and `image_generation`.

## 0.8.0 — 2026-11-14

**Layout building blocks (gh #52).** Things that sit side by side are now blocks that flow
with the document. Before, the only tools that fit were pins and raw HTML: in a real-model
evaluation of 0.5.17, a one-page sales sheet took 266 tool calls, 190 of them pinning blocks to
pixel positions, and still came out wrong.

- **Columns (`row` block).** 2 to 4 cells side by side, each one Markdown:
  `{"cells": [{"markdown": "..."}, ...], "widths": [1, 1, 2], "gap": 6}`. Cells are equal unless
  you give relative widths; the gap (mm) defaults to the document's column gap. The `cards`
  style puts each cell in a tinted box.
- **Number tiles (`stat` block).** A large value with a small label under it:
  `{"items": [{"value": "99.99%", "label": "delivered within 60 seconds"}, ...]}`, up to six. The
  `plain` style drops the tinted boxes.
- **`band` style for headings and callouts.** A band heading is a solid header strip; a band
  callout is a call-to-action box with centred text.
- **`layout.fill`.** The colour behind a band, a row's cards or the tiles: a theme colour by
  name (`accent`, `text`, `muted`, `heading`, `callout`, `figure`, `rule`) or a hex colour. The
  text on it turns white or dark, whichever reads. A fill on any other block type is refused.
- **Editor.** Add block → **Columns** or **Number tiles**; cells and tiles are edited in the
  Block panel, which saves when you leave them. **Style → band** and **Fill** are in the same
  panel. The marketing one-pager template now starts with a row, tiles and a band.
- **Agent.** `insert_blocks`, `propose_insert` and `propose_edit` take the new blocks (a row as
  JSON, a list of cells, or Markdown with a line of `---` between cells; tiles as JSON or one
  `value | label` per line), `set_block_layout` and `propose_insert` take `fill`, and a row or
  tiles are full width unless told otherwise. The prompt and the tool descriptions say to use
  these, never pins, raw HTML, tables or drawn SVG, for columns, tiles, bands and boxes. The
  same tools serve MCP clients.
- **Layout.** Each is one atomic block, measured at its width like a table; the paginator is
  unchanged. A row or tiles never split across pages, and one taller than a page is flagged as
  overflow. Cells that would be narrower than an inch stack; tiles that don't fit one line take
  two.
- **Exports.** PDF, PNG and HTML through the same print route, with a pixel-parity test for
  each new block. Word writes a row and the tiles as borderless tables and shades a band.
  PowerPoint shows a row, the tiles and a band as pictures where the paginator placed them.
- **Import** maps nothing to these blocks.

Also:

- **A pending insert with a layout its block can't take is refused when it is proposed.** A
  variant or a fill that doesn't apply to the block type used to be accepted as a suggestion
  and fail only when the author accepted it.
- **One bad item no longer loses the rest of an `insert_blocks` batch.** Content that can't be
  parsed (a chart that isn't JSON, a row with one cell) is reported for that item and the
  others are still inserted.

## 0.7.0 — 2026-11-14

From a real-model evaluation of 0.5.17: a strong model wrote four multi-page documents through
the editor, and this release fixes what went wrong.

**The co-author sees what it has proposed, and its batches keep their order.**

- **Fixed: a batch of new blocks landed in reverse order once accepted.** Blocks from one
  `insert_blocks` call now stay in the order written however you accept them, and the editor
  shows them in that order while they wait. **Accept all** applies suggestions in dependency
  order (chained moves included) and lists anything it could not apply with the reason, in the
  toast and in `failed` of `POST /api/docs/{id}/suggestions/accept-all`.
- **The agent sees its pending suggestions.** `get_document` lists them where they will land,
  marked PENDING; `read_blocks` shows the accepted text next to the pending edit; `view_pages`
  renders a preview with them applied through the same print route (`/print/<id>?preview=pending`,
  `GET /api/docs/<id>?preview=pending`). A pending insert goes by its suggestion id (`sug_…`)
  and can be revised (`propose_edit`), restyled (`set_block_layout`), redrawn (`draw_figure`),
  withdrawn (`propose_delete`) or followed (`after_block_id`). A suggestion that changes
  nothing, or repeats one still waiting, is not recorded.
- **Model defaults.** The built-in agent caps each call's output (`[codraft] max_output_tokens`,
  default 16000, `LANGSTAGE_CODRAFT_MAX_OUTPUT_TOKENS`), so a gateway no longer reserves the
  model's whole output window and fails a low-credit key with 402. Reasoning is on at low
  effort where the model has a switch (`[codraft] reasoning = auto | off | low | medium | high`,
  `LANGSTAGE_CODRAFT_REASONING`); if the provider refuses it, the agent carries on without it
  and logs why.
- **GPT-5 and o-series models reason with tools:** the built-in agent uses the Responses API
  for them (needs langstage-core 1.0.64). If an account or a gateway has no Responses API, the
  agent carries on over Chat Completions without reasoning.
- **New tool: `calculate`.** Arithmetic for totals, percentages and table cells, so figures are
  computed, not guessed.
- **Each turn starts with what you changed.** The agent is told which suggestions you accepted
  or rejected (with your reason) and which blocks you edited, added or deleted since its last
  turn. Where its last turn ended is saved, so the note survives a restart. A wrong id now gets
  an error that says what was passed and which id to use. `CodraftMiddleware(turn_notes=False)`
  turns the note off.
- **Research tools.** `fetch_url` returns a PDF's text, decodes HTML entities and pages long
  documents with `offset`; a failed `web_search` says what to do instead of retrying.
- **Co-author guidance.** It no longer cuts your content to fix a layout, drafts one block per
  paragraph, keeps revision talk out of the document, and applies a "throughout" comment to
  every matching block. `view_pages` says which pages it left out (`from_page` shows the rest);
  `propose_edit` takes `heading_level`.

**What the page looks like.**

- **Captions wrap.** A figure's caption, its label and title, and its source line were cut off
  after one line. They take the lines they need and the paginator measures them. A figure with
  a fixed height keeps it: the picture or chart gives up the room.
- **Tables keep words whole.** Columns could shrink to one letter, so words broke mid-word
  ("Buildin g"). A column is now at least as wide as its longest word. A table too wide for
  its column breaks long words at syllables, then is set smaller, then breaks anywhere. A
  Markdown table in a text block looks like a table block and keeps its alignment row; a table
  block takes `"align": [...]`. Word export writes Markdown tables as real tables.
- **Section headings follow the document.** The `section` and `rule` heading styles forced
  capitals, body size and the label font whatever the document said. A heading case, weight,
  scale or font the document sets itself now wins. `display` and `label` are unchanged. Each
  style has a note on what it does (Block panel, `block_variants` in `/api/config`,
  `store.VARIANT_NOTES`), and the agent's outline (`get_document`) now carries those notes, so
  it knows what a variant looks like before it says so. A saved theme remembers which heading
  settings were chosen.
- **Restore a deleted block.** Undo on the toast, or Document → Recently deleted: the block
  returns where it was, with its history. `GET /api/docs/{id}/deleted-blocks`,
  `POST /api/blocks/{id}/restore`, `Store.deleted_blocks`, `Store.restore_block`. The agent has
  the same two actions as tools, `list_deleted_blocks` and `restore_block` (also over MCP).
  A restore applies immediately in either approval mode: it puts your own block back as it
  was and writes nothing new.
- **Hyphenation that matches the export.** Justified text is hyphenated by default (Design →
  Hyphenation; the agent sets it with `set_typography(hyphens="auto" | "manual")`). Codraft
  places the break points itself, so the editor and the PDF break the same words; before,
  `hyphens: auto` worked in desktop Chrome and not in exports. In a PDF the break points are
  tagged soft hyphens; a text extractor that ignores the tag (pypdf) shows a space there.
- **Superscript and subscript.** `x^2^` and `H~2~O`, as well as `<sup>`/`<sub>`; kept by the
  rich-text editor and exported to Word and PowerPoint. Only `~~two tildes~~` strike text out.
  The agent is told the same dialect (0.6 told it `^2^` prints literally).
- **A block with a table, picture or raw HTML opens as Markdown.** The rich-text editor used
  to drop those when the block was closed.
- **Readable chat.** A long run of tool calls folds into one line, and each call names its
  block by its words, not its id.

**Fixed.**

- **A Codraft PDF uploaded as reference material read as broken words** ("or gan i sa tions").
  A PDF printed by Chromium (every Codraft export, and a browser's "Save as PDF") is now read
  with PDFium, which honours the soft-hyphen tags; words broken at a line end are put back
  together. Other PDFs are read with pypdf as before.
- **Uploads were lost when the data directory was opened from another directory.** An upload's
  path was saved relative to the directory the server was started in, so `export --data-dir`
  from elsewhere, or a server started from a different directory, could not find pictures and
  reference files. Paths are now absolute, and a saved path that no longer points at a file
  (an older relative one, or a data directory that was moved) is looked up in the data
  directory's own `uploads/` folder. Existing data directories need no migration.

`CODRAFT_TOOLS` now has 27 tools: `calculate`, `list_deleted_blocks` and `restore_block` are
new since 0.6.0. **Requires `langstage-core>=1.0.64`** (was `>=1.0.63`).

## 0.6.0 — 2026-11-13

- **Import an existing document (gh #45).** A Markdown (`.md`) or Word (`.docx`) file becomes a
  Codraft document: `langstage-codraft import FILE [--title] [--template] [--theme] [--split
  paragraph|section] [--json]`, **New document → Import Markdown or Word file** in the editor,
  and `POST /api/docs/import`. Headings, paragraphs, lists, quotes, code, tables, pictures, page
  breaks, GitHub alerts (as callouts) and footnotes (as per-block references plus a References
  block) map to the existing block types; a leading H1, a front-matter `title:` or Word's Title
  becomes the document title. The chosen template supplies the look, not its placeholder text.
  Imported blocks are the author's content, not suggestions. Anything that can't be represented
  is imported as plain text or left out and listed (`import.notes`, stderr, or a list in the
  editor); a file that can't be imported is refused with a one-line reason. Nothing is fetched:
  remote pictures stay links, a Markdown file's local pictures are read only from its own folder
  (CLI) or from the files sent with it, SVG is sanitized, and sizes are capped (25 MB per file,
  5,000 blocks, 400,000 lines, 300 MB for a `.docx` once unzipped). New: `Store.insert_blocks`
  appends many blocks in one transaction.
- **Export and `view_pages` without a running server (gh #44).** `langstage-codraft export
  DOC_ID -f pdf -o out.pdf --data-dir .codraft` renders straight from the data directory: it
  starts the app in-process on a private loopback port, prints through the same page route as
  the editor's Export button, and shuts down. `--standalone` does the same for the configured
  data directory; `--port` / `--url` still export through a running server; with neither,
  `export` uses the server on the configured port if one is running and renders in-process if
  not. Several documents and formats share one browser launch (`export DOC1 DOC2 -f pdf -f png
  -o out/`). `langstage-codraft mcp` over stdio now serves `view_pages` on its own, starting the
  same renderer the first time the tool is called. From Python: `export_document(data_dir,
  doc_id, "pdf")` and the `Standalone` context manager. The in-process app listens on 127.0.0.1
  only, on a port the OS picks, and every request to it needs a one-off password.
- **Fixed:** on a password-protected server the HTML export left uploaded images out of the zip.
- **Requires `langstage-core>=1.0.63`** (was `>=1.0.50`), so an install picks up the shared
  fixes since then, including finished-message order for agents that run as subgraph nodes.

## 0.5.17 — 2026-11-07

- **Fixed: a template `check` called ok then failed on every create path (#41).** A
  `templates_dir` template now gets the same checks as a new document's blocks when it loads:
  the block type, its content fields, its layout (span, role, pin, variant) and `refs`. A
  template that fails is skipped with the reason, as invalid JSON already was (#27): it isn't
  offered in the New document dialog or `/api/config`, and the server logs why at startup.
  - `check` prints `[warn] templates: …/status.json skipped: blocks[0].type 'quote' is not a
    block type (text, heading, …)`.
  - `new --template status` exits 2 with `template 'status' was skipped: <file>: <reason>`
    instead of a traceback, and so do MCP `create_document` and `POST /api/docs` (400).
  - MCP `create_document` now returns `ERROR: <reason>` for any refusal while creating (for
    example `columns` out of range) instead of an opaque tool error.
- **Fixed: a malformed deployer theme value broke the Word and PowerPoint exports.** A
  `[codraft.themes.*]` value such as `font_size = "big"` passed `check`, then made the
  `.docx` and `.pptx` exports fail. Deployer theme values now get the checks the editor's
  theme controls use. A bad value is dropped (the theme keeps its other values), `check`
  prints `[warn] themes: themes.house.font_size ignored: font_size must be a number`, and the
  server logs it at startup.
- `check` now warns when `custom_css` names a `.css` file that doesn't exist; the editor
  would otherwise run with no custom CSS and no notice outside the server log.

## 0.5.16 — 2026-11-06

- **Fixed: a pending suggestion on a deleted block was stuck (#39).** If the block an agent
  suggestion targeted was deleted (by you, or by accepting the agent's own delete while it also
  had an edit or move pending on that block), the suggestion stayed pending with nothing to
  apply it to. Accept returned a raw `block … not found` error, Accept all dropped it without
  saying so, and the pending count never cleared.
  - Deleting a block now closes its pending suggestions (status `obsolete`), so the Review tab
    and the agent stop counting them. A pending insert or move that was to follow the deleted
    block now follows the block before it (or goes to the start).
  - Suggestions left pending on deleted blocks by older versions are closed the same way when
    the data directory is opened.
  - Anything still pending with nothing to apply to (for example, an old insert anchored after
    a deleted block) is marked in the API (`gone`, with the reason). Accept refuses it with a
    400 that says why, and the review card and block popover show "Can't be applied" with only
    a **Dismiss** button. `accept-all` lists it in `skipped` and in the new `skipped_gone`, and
    the toast says "skipped N on deleted blocks".

## 0.5.15 — 2026-11-05

- **Fixed: accepting an agent edit made before you edited the block reverted your newer edit
  (#37).** A suggestion recorded no base version, so if you kept writing in a block while the
  agent's suggestion waited (the normal case during a long "Address all comments" turn),
  accepting it wrote the agent's text, based on the old content, over yours. **Accept all** did
  it without showing anything, and the review card's diff showed the agent "deleting" text it
  never saw.
  - Edit and delete suggestions now record the block version they were based on. Once the
    block moves on, the suggestion is **outdated**: the review card and block popover say so
    and diff it against the text the agent saw.
  - **Accept all** skips outdated suggestions and reports how many it skipped (REST:
    `{"count": n, "skipped": [ids]}`).
  - A single accept of an outdated suggestion is refused with a 400 unless you choose
    **Accept anyway** (REST: `{"force": true}`). It then lands as a new branch off the version
    the agent saw, like a concurrent human edit, so your newer version stays in the block's
    history.
  - Suggestions stored by earlier versions have no base version and accept as before.
- **Fixed: `POST /api/docs` with an unknown template id created an empty blank document
  (#36).** It now answers 400 with the same `unknown template '…'; available: …` message the
  CLI and MCP give. A missing or empty `template` still means blank.

## 0.5.14 — 2026-11-02

- **Fixed: one block or suggestion with a wrong-typed field blanked the whole editor and hung
  exports (#34).** A suggestion with `"markdown": ["Note one", "Note two"]`, or a block whose
  heading `text` was a number, took the editor down to a white page (so **Reject** was
  unreachable), and once accepted made every PDF/DOCX export wait 30 s and return a bare 500.
  0.5.11 checked that `content` is an object but not the fields inside it.
  - Every write path (REST insert and edit, `propose_*`, `insert_blocks`, MCP, and suggestions
    when they are proposed rather than when accepted) now refuses a wrong-typed field with a 400
    or an `ERROR:` that names it, e.g. `content.text of a heading block must be text`. Checked:
    the text fields of every block type, `video.loop`/`muted` and `table.header` (true/false),
    heading `level` (a whole number 1–6), table `rows` (a list of lists of cells) and chart
    `spec` (an object). A list of strings for `markdown` is joined into paragraphs.
  - Values stored by an older version are read back as text (a list is joined, a number becomes
    its digits, anything else is empty), so existing documents open and export again.
  - Each block and each review card renders inside its own error boundary, so a block that
    still fails to render can't take the page or its Reject button with it.
  - An export whose print view throws now fails within a few seconds with the page's error,
    instead of after a 30 s timeout.
- **Fixed:** `PATCH /api/docs/{id}` with a non-object `settings.style` answered 500
  (`AttributeError`); it is now a 400 like the other settings.

## 0.5.13 — 2026-11-01

- **Fixed: on A4 and A5, every other exported page image started with a sliver of the page
  above (#32).** With a dark cover, page 2 of a PNG, JPG, GIF or MP4 export had a navy line
  along its top and was 2 px taller than its neighbours; the agent's `view_pages` screenshots
  had the same line. The cause was that an A4 page is 1122.52 CSS px tall, so pages after the
  first sit at fractional offsets, and an element screenshot rounds its clip outward. Each
  page is now captured from exactly the rows Chromium paints for it, and a page that paints
  one row short is padded with its own margin. Every image is now the same size and contains
  only its own page. The layout is unchanged, so the PDF is the same and exported pages still
  match the editor pixel for pixel; the parity checks now cover page 2 on A4 and Letter.

## 0.5.12 — 2026-10-31

- **Fixed: an unknown page size was accepted, reported as applied, and then rendered as Letter
  (#30).** `set_document_style(page_size="A3")` answered "Style updated: … a3 portrait", and
  `PATCH /api/docs/{id}` stored `"bogus"`. `default_page_size = "A4"` was shown by
  `--show-config` and then ignored. Now:
  - The agent tool and MCP refuse an unknown size with an `ERROR:` that lists letter, a4,
    legal, a5 and slide, and say to use `page_w_mm` + `page_h_mm` for any other size.
  - The API refuses it with a 400. "A4" is read as a4.
  - In config, `default_page_size` ignores case, and an unknown value falls back to Letter
    with a note. `approval` is validated the same way, so "Auto" is read as auto and an
    unknown value falls back with a note instead of breaking New document.
  - A template with an unknown size is skipped and reported like any broken template (#27).
  - `get_document` shows a size stored by an older version as the Letter it renders.
- **Fixed: other settings the renderers would ignore or choke on are refused too.** The API
  stored any of these unchecked, now each gets a 400 naming the field and the allowed range:
  - non-numeric or out-of-range `margins` (0–80 mm), `block_gap` and `column_gap` (0–30 mm),
    `page_number_start` and `min_pages`;
  - a non-boolean `number_cover_pages`;
  - a `brief` that isn't text fields;
  - `page_w_mm` / `page_h_mm` without `page_size: "custom"`;
  - a `cover`, `back`, `header` or `footer` that isn't an object.

  The checks look only at the fields a change sets, so a document saved with an odd value by
  an older version can still be edited.
- **Changed: a `[codraft.doc_defaults]` setting no document could use is left out.** New
  documents no longer fail because of it; `check` and the server log say which setting was
  left out and why.

## 0.5.11 — 2026-10-30

- **Fixed: a refused block insert left a "ghost" block behind (#28).** `POST
  /api/docs/{id}/blocks` with `content` that isn't an object (`"Hello"`, a list) returned 500 after
  the block row was already written, and the next write committed it: a block with no versions
  whose history 500'd. Content is now checked first (400, `content must be an object, e.g.
  {"markdown": "..."}`), for edits too. More broadly, every store method now runs as one
  transaction: if it fails part-way, everything it wrote is rolled back instead of waiting for
  the next commit. That also fixes a move after a block that doesn't exist, which was refused
  but still moved the block to the top on the next write, and a template whose blocks fail
  validation, which could leave a half-made document. An upload whose database write fails no
  longer leaves its file behind.
- **Fixed: a `templates_dir` template with invalid JSON vanished without a word (#27).** `check`
  now warns for each skipped file with the reason (`invalid JSON: … line 1 column 120`, not an
  object, bad `blocks`), `new --template` and the MCP `create_document` tool name the file and
  its error instead of "unknown template", and the server logs a warning at startup.

## 0.5.10 — 2026-10-28

- **Fixed: block tools now stay inside the document they're given (#25).** `set_block_layout`,
  `read_blocks` and `get_block_history` accepted a block from any document, so an agent or MCP
  client with a stale or wrong `doc_id` could restyle, or read, a different document. They now
  refuse with `ERROR: block … is not in document …`, as `propose_*` already did. An audit found
  three more gaps, now closed the same way: a suggestion's `comment_id` could name another
  document's comment (and accepting it resolved that comment); `propose_insert` and
  `propose_move` accepted an anchor block from another document; and a comment (tool or
  `POST /api/docs/{id}/comments`) could be pinned to another document's block.
- **Fixed: the demo co-author's hint read "write a paragraph about ," (#24).** The `<topic>`
  placeholder was dropped by the chat's HTML sanitiser; the hint now gives a real example.

## 0.5.9 — 2026-10-26

- **Fixed: over MCP, `view_pages` and `view_upload` failed with a bare "Error executing tool"
  (#17).** When Chromium wasn't installed or an upload id didn't exist, MCP clients such as
  Claude Code and Cursor saw no reason. The cause was only on the server's stderr. Both tools
  now return the same `ERROR: …` text as every other tool, e.g. "Chromium is not installed for
  export/vision. Run: langstage-codraft install-browser". The agent's own `view_pages` and
  `view_upload` already did this.

## 0.5.8 — 2026-10-24

- **Fixed: a chart whose `$schema` vega-embed can't parse showed "Chart error: Cannot read
  properties of null (reading 'slice')" (#20).** An agent wrote a valid Vega-Lite spec with a
  non-canonical `$schema` URL, and the chart broke in the editor and every export. The error
  also misled the agent into proposing a no-op fix. Charts are Vega-Lite by contract, so a
  `$schema` that isn't a recognisable vega/vega-lite schema URL is now ignored and the chart
  renders. Existing documents with such a spec render without edits.

## 0.5.7 — 2026-10-24

- **Fixed: a heading could be stranded at the bottom of a column or page (#18).** When a heading
  fit in the space left but the start of the block after it didn't, the heading stayed behind
  and its paragraph started in the next column or page. Headings now keep with the next block:
  a heading moves on with the first paragraph (or the whole chart, table or image) it
  introduces, and consecutive headings move together. A heading already at the top of a column
  stays where it is. The editor, preview and every export share the paginator, so they all
  change together. Some documents now break columns or pages at a different place.

## 0.5.6 — 2026-10-24

- **Fixed: `allowed_themes` wasn't enforced on new documents (#15).** The built-in proposal, memo,
  one-pager and paper templates kept their own theme (modern, minimal, bold, academic) even when
  `allowed_themes` left it out, and `POST /api/docs` stored any theme in `settings`. A new
  document's template (or house) theme that isn't offered now becomes `default_theme`, and
  `POST /api/docs` refuses a theme that isn't offered with 400, as `PATCH` already did.
  `langstage-codraft new` now applies `[codraft.themes]` and `allowed_themes` too.
- **Fixed: a document whose theme isn't offered rendered in the first built-in left.** A stored
  theme that `allowed_themes` excludes (or a deleted saved theme) now draws with `default_theme`,
  then classic, then the first theme offered, in the editor, the export and the toolbar chip alike.

## 0.5.5 — 2026-10-21

- **Fixed: a document created over MCP ignored the house style (#12).** MCP's `create_document`
  (stdio and `/mcp`) now goes through the same `new_document()` as the editor's New document and
  `langstage-codraft new`. It applies `[codraft.doc_defaults]` (and `create_app(doc_defaults=...)`),
  `default_page_size`, `default_theme`, `approval` and the title on the cover.
- **Fixed: the editor and the export could paginate differently (#13).** Empty blocks show a hint
  in the editor (the empty References line, "Heading", "Type something…", "Callout…"), but the
  export rendered nothing there. Every block below landed a line higher in the PDF, and near a page
  boundary the editor showed one more page. The export now lays out the same text invisibly, so
  both measure the same box. An empty paragraph, heading or callout now keeps its line in the
  export, as it already did in the editor. A new e2e test compares every block's page and offset
  in the editor and the print view.

## 0.5.4 — 2026-10-19

- **Security: DNS rebinding guard (langstage#208).** A page on another site could point its own
  domain at 127.0.0.1 and then reach a local Codraft as if same-origin, reading documents and
  making changes. The cross-site guard can't see it. `langstage-codraft run` on a loopback
  address (the default) now answers only to `localhost`, `127.0.0.1`, `[::1]`, `*.localhost`,
  the bind host and the new `allowed_hosts` setting (`[server] allowed_hosts` in
  `langstage.toml`, or `LANGSTAGE_ALLOWED_HOSTS`, comma-separated). Every other `Host` gets a 403
  that says how to allow it. `"*"` turns the check off, and `*.example.org` matches subdomains.
  It covers every method, `/mcp` included. It's the same setting as langstage-web's, so one
  `langstage.toml` covers both.
  - **If a reverse proxy forwards its public name as `Host` to a loopback-bound `run`** (Caddy
    and Traefik do by default), add that name to `allowed_hosts`.
  - Apps that mount Codraft with `create_app` are unaffected; they opt in with
    `host_guard=True`.

## 0.5.3 — 2026-10-19

- **Security: setting `proxy_secret` turned the password off (#8).** With both `proxy_secret`
  and `auth_password` set, basic auth was never checked, so behind a same-host proxy nothing
  needed credentials. The two checks are now independent: the password applies to every
  request, and the proxy secret is checked on top of it for requests from other machines.
  The export renderer already sends both.
- **Fixed: `[ui.features] mcp = false` only hid the MCP URL (#9).** The `/mcp` endpoint is now
  not served at all. `langstage-codraft mcp` (stdio) is unaffected. `chat = false` still hides
  only the chat tab: the agent buttons (Ask, Address comments, Find sources) run through the
  same chat endpoint, so the README now says so.

## 0.5.2 — 2026-10-18

Fixes for what the first nightly dogfood run found.

- **Security: basic auth applied only to requests from other machines (#2).** With
  `LANGSTAGE_AUTH_PASSWORD` set, any request from 127.0.0.1 skipped it, so behind a same-host
  reverse proxy (every visitor arrives from loopback) the password protected nothing. Basic auth
  now applies to every request. The export renderer, the `mcp` command's renderer and
  `langstage-codraft export` send the configured password (and proxy secret) themselves; the
  renderer sends them only to Codraft's own origin. The proxy secret keeps its exemption for this
  machine, the host app in front of Codraft.
- **Security: another website could accept the agent's suggestions (#3).** A plain cross-site
  `<form>` POST needs no CORS preflight, so it could run `suggestions/accept-all`, uploads or
  `POST /api/docs`. State-changing requests whose `Origin` is neither Codraft's own nor a
  loopback origin, or marked `Sec-Fetch-Site: cross-site` with no `Origin`, are now refused with
  403 (langstage-web's guard from langstage#206). curl, the CLI and MCP clients are unaffected.
- **Fixed: `langstage-codraft new` ignored `[codraft.doc_defaults]`, `default_page_size` and the
  title (#4).** It now shares one helper with the editor's New document, so a CLI document gets
  the house cover, header and footer, the default page size and the title on its cover.
- **Fixed: `list` and `--show-config` crashed on a cp1252 console (#5)** when a title or setting
  had characters it can't show. CLI output goes through langstage-core's `safe_print`.
- **Fixed: a relative `[ui] custom_css` path resolved against the current folder (#6).** It now
  resolves against the `langstage.toml` that set it, like `data_dir` and `templates_dir`, so
  branding survives a run from a subdirectory. Inline CSS is unchanged.

## 0.5.1 — 2026-10-18

- **Fixed: `langstage-codraft export` rejected docx, pptx, gif and mp4.** The README lists all
  eight formats and the server exports them, but the CLI only offered pdf, png, jpg and html.

## 0.5.0 — 2026-10-18

Design the whole document, not just its blocks.

- **Cover pages you control.** A switch turns the separate cover off: the cover blocks (title,
  subtitle) then open page 1 as a full-width title block and the body starts right below.
  Covers (and back pages) take a background colour or image, top/middle/bottom placement and
  pinned art or logos; dark backgrounds get light text. "+ Add to cover" sits above the page.
- **Fixed: nothing could be added to a cover.** A block inserted after a cover block, by a
  person or the agent, now joins the cover (it used to land on page 2), and `propose_insert`
  takes a `role`.
- **Fonts and colours per document** (the new Design tab): body, heading, title and label
  fonts, sizes, line spacing, heading scale/weight/case, alignment, and text, heading, accent,
  rule, tint and paper colours, on top of the theme. 13 more bundled families (Raleway, Lato,
  Open Sans, Montserrat, Roboto, Merriweather, Lora, Playfair Display, EB Garamond, Libre
  Baskerville, Carlito, Arimo, Tinos) and any Google Fonts family on demand (`web_fonts`).
  **Save as theme** stores a document's look for every other document.
- **Running header and footer** as settings: left/centre/right text in the margins with
  `{page}`, `{pages}`, `{title}`, `{year}`; Word export writes them as real header/footer fields.
- **Match an uploaded document's look.** Upload a PDF, Word file or page image as "style":
  Codraft reads its page setup, fonts (mapped to open look-alikes), colours, cover and running
  header/footer and applies them. PDFs are rendered, so the agent sees their pages.
- **Agent tools:** `set_typography`, `set_cover`, `set_header_footer`, `match_upload_style`; the
  outline reports the cover, style overrides and header/footer.
- **Word export** carries the document's fonts, sizes and colours onto Word's styles.
- **For hosts:** `create_app(doc_defaults=...)` / `[codraft.doc_defaults]` set what new
  documents start with; themes take `color_heading` and `cover_*` keys; `[ui.features]
  style_editor = false` hides the font and colour editor.
- The side panel shows icons with the active tab's label when it's narrow.
- **The agent goes by its own name.** When the graph has one (`create_agent(..., name="Juniper")`,
  `create_deep_agent(..., name=...)`), the editor uses it everywhere it used to say "agent" or
  "co-author": Ask Juniper, Suggestions from Juniper, Tag Juniper, its comment replies and
  version history. LangGraph's default name ("LangGraph") is ignored; an explicit
  `[ui] agent_name` still wins.

## 0.4.3 — 2026-10-18

More from the Lighthouse dogfood (second pass, against 0.4.2).

- **Fixed: a new document's kickoff could vanish from the chat.** When the chat history
  arrived after the kickoff started (common behind a slower proxy), it replaced the running
  turn, which then carried on out of sight. Sending now waits for the history.
- **Messages sent mid-turn are queued** and go when the turn ends; pressing Enter while the
  agent worked used to do nothing.
- **House style wins over the built-in templates' generic look:** with a `default_theme`, the
  report and newsletter templates use it instead of `classic` (templates with their own look,
  such as academic, keep it), and `default_page_size` beats a built-in template's paper size.

## 0.4.2 — 2026-10-18

Fixes from dogfooding Codraft embedded in Lighthouse (a real host app, two signed-in users,
the host's own agent).

- **Fixed: the agent could loop after looking at pages.** Page screenshots from `view_pages`
  were re-appended as the newest message on every model call in the turn, so the agent kept
  reacting to them as fresh input ("Now I can see the pages…") instead of acting. Images now
  sit right after the tool results that produced them.
- **Fixed: structured content sent as a JSON string became literal text.** A references block
  showed `{"title": …}` as its heading, and the documented callout form `{"markdown", "tone"}`
  was read as Markdown. JSON using the block type's own keys is now read as content.
- **A failing tool no longer ends the turn.** `CodraftMiddleware(tool_errors=True)` (default)
  hands a tool's exception back to the model as its result, so the agent can work around it.
- **Names:** comments, replies and new blocks credit the signed-in user (`user_header`) or the
  collaborator's display name instead of "user"; the agent appears under `ui.agent_name` in
  comments, history and help text. A mounted app with no `info` labels the agent by name.
- **Embedding:** in an iframe Codraft posts `codraft:location` (for deep links) and
  `codraft:auth-required` to the host page, and navigates with `replaceState` so the host's
  Back button leaves the page. An expired host session shows a clear banner instead of
  "Unexpected token '<'". Deleting a document stays under the mount path.
- **Deployers:** `create_app(..., doc_themes={...})` adds document themes in code;
  `default_page_size` (`[codraft]`) sets the paper for new documents. A partial `[ui.colors]`
  palette derives the panel, canvas, border and hover shades from `surface` and `primary`, and
  light colours no longer leak into dark mode. A broken `icon_url` falls back to the pen tile.
- **Small screens:** the new-document dialog keeps its buttons in view (they were below the fold
  at 768px); the document list starts closed under 900px wide.
- **Templates:** describing what you want when starting from a template now asks the agent to
  adapt the template's headings, as it already did for blank documents.
- The README's embedding example is now a complete, runnable `host.py`.

## 0.4.1 — 2026-10-18

- **Fixed:** a trusted proxy's `X-Forwarded-Prefix` (sent with the proxy secret) now takes effect
  even when the proxy connects over localhost. Before, such requests were treated as the export
  renderer and the page loaded its assets from the wrong path (a blank page).

## 0.4.0 — 2026-10-18

Embed Codraft in another app, with that app's look.

- **App branding** (the chrome only; documents keep their themes): `[ui]` title, subtitle,
  agent name, welcome message, icon, font (`font_family`, `font_url`), starter prompts,
  `custom_css`, and `[ui.colors]` / `[ui.colors_dark]` overriding any design token. The same keys
  can be passed in code as `create_app(..., ui={...})`. `[ui.features]` turns off chat, MCP, the
  document list or the theme toggle.
- **Embedded mode** (`ui.embedded`, or `?embed=1`) drops the Codraft brand and theme toggle, so
  the host page's navigation frames the editor.
- **Sub-path hosting:** the frontend works under any prefix. `mount(host_app, "/write", app)`
  serves Codraft inside another FastAPI app and runs its startup and shutdown inside the host's
  lifespan. `renderer_url` points exports at the mounted print route.
- **Access:** `proxy_secret` (a shared header from a trusted proxy; local render requests are
  exempt), HTTP basic auth (`auth_username` / `auth_password`, as in langstage-web), and
  `user_header`. With `user_header`, the host's signed-in user names edits and presence, and
  can't be impersonated from the browser. `--show-config` masks secrets.
- The server now uses a FastAPI lifespan instead of the deprecated `on_event` hooks.

## 0.3.1 — 2026-10-18

- **Fixed: blank pages at the end of PDF exports.** The hidden copy of the document used to measure
  blocks extended the page, and Chromium's print-to-PDF added a blank page for every page of it
  (a 7-page report exported as 15). It is now zero-sized; layout and other exports are unchanged.
  A regression test checks that PDF and PNG exports have the same number of pages.
- **Page numbering options.** Cover and closing (back) pages are no longer counted, so the first
  body page is page 1 (or your starting number), and `{pages}` counts only numbered pages. Turn on
  "Number cover and closing pages" to count them. "Pages without a number" lists positions whose
  page-number footer is hidden and which don't use a number. Available in the Document panel and
  through `set_document_style` (`number_cover_pages`, `unnumbered_pages`).
  **Behavior change:** in documents with a cover page, body pages now number from 1 instead of 2.

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
