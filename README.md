# langstage-codraft

**The document stage for your LangGraph agent.** Draft a document block by block on real pages.
Tag your agent in comments, approve every change it suggests, and export exactly what you see.

[![Tests](https://github.com/dkedar7/langstage-codraft/actions/workflows/test.yml/badge.svg)](https://github.com/dkedar7/langstage-codraft/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/langstage-codraft)](https://pypi.org/project/langstage-codraft/)
[![Python](https://img.shields.io/pypi/pyversions/langstage-codraft)](https://pypi.org/project/langstage-codraft/)

![Codraft editor: an agent suggestion shown as a diff on the block it changes](https://raw.githubusercontent.com/dkedar7/langstage-codraft/main/docs/assets/editor.png)

<details><summary>Dark mode</summary>

![Codraft in dark mode](https://raw.githubusercontent.com/dkedar7/langstage-codraft/main/docs/assets/editor-dark.png)

</details>

You own the thinking. Your agent owns the styling, and helps with drafting and research when
you ask it to.

- **Blocks on pages.** Text, headings, images, tables, Vega-Lite charts, callouts and an
  automatic references list. Pages come in Letter, A4, Legal, A5 or 16:9 slide, with 1–3
  columns. You can also pin blocks so other blocks flow around them, repeat headers and logos
  on every page, and add cover and back pages. Pageless mode is available too.
- **The agent edits by suggestion.** Every change the agent makes to your text arrives as a
  diff you accept or reject. A per-document switch turns on auto-apply.
- **Comments tag the agent.** Queue comments for later or ask now. "Address all comments"
  handles them strictly top to bottom.
- **Nothing is overwritten.** Each block keeps a version tree. Step back, step forward, and
  edit an old version to start a new branch.
- **Sources stay with each block.** The agent cites as it writes, and a `references` block
  compiles them.
- **The agent sees the page.** `view_pages` gives a vision model screenshots of the rendered
  pages, so it can fix layout problems you'd spot by eye.
- **Export matches the screen.** PDF, PNG/JPG per page, or a self-contained HTML zip, all
  rendered by the same component as the editor (CI checks them pixel for pixel). Also Word
  (`.docx`, editable and reflowing), PowerPoint (`.pptx`, one slide per page, with editable text
  boxes where the paginator put them), and an animated GIF or MP4 page slideshow.
- **Sketch it, get an image.** Draw on an image block; the agent looks at the sketch
  (`view_sketch`) and turns it into an image with its image tool, or says it can't.
- **Video and GIF blocks.** They play in the editor and in the HTML export; PDF and images use
  the poster frame.
- **Write together, live.** Everyone in a document sees each other's cursor block, edits from
  other tabs, people and agents arrive instantly, and a block someone is editing is soft-locked.
  If two people still edit the same block, both versions are kept as branches.
- **Any agent, over MCP.** Claude Code, Cursor or any MCP client can co-author through the same
  approval-gated tools, at `http://localhost:8060/mcp` or `langstage-codraft mcp` (stdio).
- **Light and dark.** The interface uses langstage-web's design system, so the two stages look
  like one product.

## Quick start

```bash
pip install langstage-codraft
langstage-codraft install-browser          # Chromium for export and the agent's eyes (once)
langstage-codraft run --demo               # keyless scripted co-author, opens http://localhost:8060
```

With a real model:

```bash
export OPENROUTER_API_KEY=...              # or OPENAI_API_KEY / ANTHROPIC_API_KEY
langstage-codraft run                      # default agent: claude-haiku-5.5 via OpenRouter
langstage-codraft run -m openai:gpt-5-mini # any LangChain model spec
pip install "langstage-codraft[anthropic]"  # for ANTHROPIC_API_KEY / anthropic: models
pip install "langstage-codraft[video]"      # MP4 export (bundles ffmpeg)
```

## Bring your own agent

Codraft is a LangStage stage: you bring the LangGraph agent and Codraft adds what it needs to
work on documents. There are three ways to plug one in.

```python
# my_agent.py  (needs OPENAI_API_KEY; any LangChain chat model works)
from langchain.agents import create_agent
from langchain_core.tools import tool
from langstage_codraft import CodraftMiddleware


@tool
def glossary(term: str) -> str:
    """Look up an internal term."""
    return {"pilot": "A time-boxed trial with a go/no-go review."}.get(term.lower(), "No entry.")


# 1. A graph that includes the middleware (it registers the tools and the co-author prompt,
#    and attaches page screenshots for vision models).
graph = create_agent("openai:gpt-5-mini", tools=[glossary], middleware=[CodraftMiddleware()])


# 2. Or a factory: Codraft calls it with its tools and middleware.
def make(tools, middleware):
    return create_agent("openai:gpt-5-mini", tools=[*tools, glossary], middleware=middleware)
```

```bash
langstage-codraft run --agent my_agent.py:graph    # or my_agent.py:make
langstage-codraft check --agent my_agent.py:graph  # preflight; --live runs one real turn
```

A graph built without the Codraft tools still chats, but it can't touch the document. `check`
tells you when that's the case.

The agent's toolset (`CODRAFT_TOOLS`) has 16 tools. Each one reads and writes through the same
API the editor uses, so the agent has no private path, and nothing the agent can do is out of
the author's reach:

| Read | Write (suggestions) | Style (applies directly) | Other |
|---|---|---|---|
| `get_document`, `read_blocks`, `search_document`, `read_upload`, `list_comments`, `get_block_history` | `propose_edit`, `propose_insert`, `propose_delete`, `propose_move` | `set_block_layout`, `set_document_style` | `reply_to_comment`, `compile_references`, `view_pages`, `view_sketch` |

Each document has its own agent thread (the AG-UI thread id is the document id). Conversations
are stored in SQLite and survive restarts. The same agent is also served at `POST /agui` for any
AG-UI client.

## Bring any agent: MCP

The same tools are served over MCP, so agents outside LangGraph can co-author too. Their edits
are suggestions the author approves, exactly like the built-in co-author's.

```bash
# with `langstage-codraft run` going (view_pages works over HTTP):
claude mcp add --transport http codraft http://localhost:8060/mcp

# or stdio, for clients that launch a command:
langstage-codraft mcp --data-dir .codraft
```

MCP tools: `list_documents`, `create_document`, every `CODRAFT_TOOLS` tool with a `doc_id`
argument, plus `view_pages` and `view_sketch`, which return images.

## For deployers

Configuration follows the LangStage chain: defaults < `langstage.toml` < `LANGSTAGE_*` env < CLI
flags.

```toml
# langstage.toml
[agent]
spec = "my_agent.py:graph"

[codraft]
model = "openrouter:anthropic/claude-haiku-5.5"   # used when no agent spec is set
approval = "required"                            # or "auto" for new documents
default_theme = "acme"
allowed_themes = ["acme", "academic"]
templates_dir = "templates"                      # *.json: settings + a block skeleton
data_dir = ".codraft"

[ui]
theme = "auto"                                   # app chrome: light | dark | auto

[codraft.themes.acme]
label = "Acme"
font_body = "Inter, system-ui, sans-serif"
font_heading = "Inter, system-ui, sans-serif"
color_accent = "#d9480f"
```

`langstage-codraft run --show-config` prints the resolved values and where each one came from.

There are seven built-in templates (ids in brackets): blank, report, proposal, memo, two-column academic
paper (`paper`), marketing one-pager (`one-pager`), and a three-column newsletter with a repeating
masthead (`newsletter`). Each new document
records the author's brief (writing style, objective, platform, audience, language, output
format), and the agent reads it before writing.

## CLI

```
langstage-codraft run [--agent SPEC | --model SPEC | --demo] [--port 8060] [--approval auto]
langstage-codraft check [--live] [--json]
langstage-codraft new "Title" --template report
langstage-codraft list
langstage-codraft export DOC_ID -f pdf|docx|pptx|png|jpg|html|gif|mp4 -o out.pdf
langstage-codraft mcp [--data-dir DIR]          # Codraft over MCP (stdio)
langstage-codraft install-browser
```

## How the layout works

Blocks are measured in the browser at the width they'll occupy. A pure function then paginates
them (see `frontend/src/layout/paginate.ts`, which has its own unit tests):

- Blocks flow column-major. A full-width block balances the columns above it.
- A pinned block reserves a rectangle on the page instead of wrapping text around it.
- Text splits only at paragraph or line boundaries, keeping at least two lines on each side.
- A block taller than a page is flagged rather than looping forever.

The preview, the export and the agent's screenshots all print that same layout in headless
Chromium. Chromium never paginates anything again, so the editor and the PDF can't disagree.

![A three-column community newsletter page with a pinned masthead that repeats on every page](https://raw.githubusercontent.com/dkedar7/langstage-codraft/main/docs/assets/newsletter-page.png)

## Part of LangStage

| Stage | Package |
|---|---|
| Web workspace | [`langstage`](https://github.com/dkedar7/langstage) |
| Terminal | [`langstage-cli`](https://github.com/dkedar7/langstage-cli) |
| JupyterLab | [`langstage-jupyter`](https://github.com/dkedar7/langstage-jupyter) |
| VS Code | [`langstage-vscode`](https://github.com/dkedar7/langstage-vscode) |
| Hermes | [`langstage-hermes`](https://github.com/dkedar7/langstage-hermes) |
| **Documents** | **`langstage-codraft`** |
| Shared host + AG-UI bridge | [`langstage-core`](https://github.com/dkedar7/langstage-core) |

Docs: https://dkedar7.github.io/langstage-docs/stages/codraft/

## Development

```bash
uv venv && uv pip install -e ".[dev]"
cd frontend && npm ci && npm test && npm run build && cd ..
pytest                                   # unit + API + browser e2e (needs the built UI)
CODRAFT_LIVE=1 OPENROUTER_API_KEY=... pytest tests/live -s    # opt-in real-model checks
```

Run `npm run dev` in `frontend/` alongside `langstage-codraft run --demo --no-browser` to get
hot reload; Vite proxies `/api` to port 8060.

The design notes this started from are in `docs/notes/`, and the requirements are in
`docs/brainstorms/`.

## License

MIT
