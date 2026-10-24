# Changelog

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
