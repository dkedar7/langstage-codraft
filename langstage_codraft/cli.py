"""``langstage-codraft`` command line.

    langstage-codraft run [--agent path.py:graph | --model provider:model | --demo]
    langstage-codraft check [--live]
    langstage-codraft new "Title" [--template report]
    langstage-codraft list
    langstage-codraft export DOC_ID --format pdf -o out.pdf   (needs a running server)
    langstage-codraft install-browser
"""
from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path

import click
from langstage_core.console import safe_print as _echo  # cp1252-safe output (gh #5)

from . import __version__
from .config import CodraftConfig


def _cfg(**overrides) -> CodraftConfig:
    return CodraftConfig.resolve(overrides=overrides)


def _ok(msg: str) -> None:
    _echo(f"[ ok ] {msg}")


def _warn(msg: str) -> None:
    _echo(f"[warn] {msg}")


def _fail(msg: str) -> None:
    _echo(f"[fail] {msg}")


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="langstage-codraft")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Codraft: the document stage for your LangGraph agent."""
    if ctx.invoked_subcommand is None:
        _echo(ctx.get_help())


_agent_opts = [
    click.option("-a", "--agent", "agent_spec", help="Agent spec, e.g. my_agent.py:graph (a graph or a factory)."),
    click.option("-m", "--model", help="Model for the default agent, e.g. openrouter:anthropic/claude-haiku-5.5."),
    click.option("--demo", is_flag=True, default=None, help="Run the keyless scripted demo co-author."),
    click.option("--data-dir", type=click.Path(path_type=Path), help="Where documents, uploads and checkpoints live."),
]


def _with_agent_opts(f):
    for opt in reversed(_agent_opts):
        f = opt(f)
    return f


@main.command()
@_with_agent_opts
@click.option("--host", help="Bind address (default localhost).")
@click.option("-p", "--port", type=int, help="Port (default 8060).")
@click.option("--approval", type=click.Choice(["required", "auto"]), help="Default approval mode for new documents.")
@click.option("--no-browser", is_flag=True, help="Don't open a browser tab.")
@click.option("--show-config", is_flag=True, help="Print the resolved config and exit.")
def run(agent_spec, model, demo, data_dir, host, port, approval, no_browser, show_config) -> None:
    """Start the Codraft web app."""
    cfg = _cfg(agent_spec=agent_spec, model=model, demo=demo, data_dir=data_dir, host=host, port=port,
               approval=approval)
    if show_config:
        _echo(cfg.describe())
        return
    from .agent import resolve
    from .server import create_app

    try:
        graph, info = resolve(cfg)
    except Exception as exc:  # noqa: BLE001
        _fail(f"could not load the agent: {type(exc).__name__}: {exc}")
        sys.exit(2)
    if graph is None:
        _warn(info.get("hint", "no agent configured; the editor works, chat is disabled"))
    elif info.get("missing_tools"):
        _warn(f"agent {info['label']} lacks codraft tools ({len(info['missing_tools'])} missing); it can chat "
              "but cannot edit the document. Add CodraftMiddleware() or export a factory(tools, middleware).")
    app = create_app(cfg, graph, info, host_guard=True)
    url = f"http://{cfg.host}:{cfg.port}"
    _echo(f"Codraft {__version__}  agent: {info.get('label')}  data: {Path(cfg.data_dir).resolve()}")
    _echo(f"Open {url}")
    if not no_browser:
        import threading

        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    import uvicorn

    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")


@main.command()
@_with_agent_opts
@click.option("--live", is_flag=True, help="Also run one real agent turn (costs a model call).")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def check(agent_spec, model, demo, data_dir, live, as_json) -> None:
    """Preflight: agent, tools, browser for export/vision, and (with --live) one real turn."""
    cfg = _cfg(agent_spec=agent_spec, model=model, demo=demo, data_dir=data_dir)
    from .agent import resolve

    report: dict = {"version": __version__, "checks": []}

    def add(name: str, ok: bool | None, detail: str) -> None:
        report["checks"].append({"name": name, "ok": ok, "detail": detail})

    graph = None
    try:
        graph, info = resolve(cfg)
        if graph is None:
            add("agent", False, info.get("hint", "no agent"))
        else:
            add("agent", True, f"{info['kind']}: {info['label']}")
            missing = info.get("missing_tools") or []
            add("tools", not missing, "all codraft tools present" if not missing
                else f"missing {', '.join(missing)} (add CodraftMiddleware() or use a factory)")
            add("checkpointer", True, "compiled-in" if getattr(graph, "checkpointer", None)
                else "none compiled in; the server attaches a durable SQLite one")
    except Exception as exc:  # noqa: BLE001
        add("agent", False, f"{type(exc).__name__}: {exc}")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            b = p.chromium.launch()
            b.close()
        add("browser", True, "Chromium available for export and view_pages")
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).splitlines()[0][:160]
        add("browser", None, f"not available ({msg}); run: langstage-codraft install-browser")

    static = Path(__file__).parent / "static" / "index.html"
    add("web-ui", static.is_file(), "bundled" if static.is_file() else "not built (cd frontend && npm run build)")

    if live and graph is not None:
        from langstage_core.agui import verify

        from .store import Store, set_store

        tmp = Path(cfg.data_dir) / "check"
        store = Store(tmp / "check.db")
        set_store(store)
        doc = store.create_document("check", {})
        res = verify(graph, message="Call get_document, then say OK.", thread_id=doc["id"])
        add("live-turn", bool(res.ok), res.reason)

    ok = all(c["ok"] is not False for c in report["checks"])
    report["ok"] = ok
    if as_json:
        _echo(json.dumps(report, indent=2))
    else:
        for c in report["checks"]:
            (_ok if c["ok"] else _warn if c["ok"] is None else _fail)(f"{c['name']}: {c['detail']}")
    sys.exit(0 if ok else 1)


@main.command("list")
@click.option("--data-dir", type=click.Path(path_type=Path))
@click.option("--json", "as_json", is_flag=True)
def list_docs(data_dir, as_json) -> None:
    """List documents in the data directory."""
    from .store import Store

    cfg = _cfg(data_dir=data_dir)
    docs = Store(Path(cfg.data_dir) / "codraft.db").list_documents()
    if as_json:
        _echo(json.dumps(docs, indent=2))
        return
    if not docs:
        _echo("No documents yet.")
    for d in docs:
        _echo(f"{d['id']}  {d['title']}  ({d['n_blocks']} blocks)")


@main.command()
@click.argument("title")
@click.option("--template", default="blank", help="Template id (see the new-document dialog).")
@click.option("--data-dir", type=click.Path(path_type=Path))
def new(title, template, data_dir) -> None:
    """Create a document from a template, exactly as the editor's New document does."""
    from . import templates as tpl
    from .server import house_defaults, new_document
    from .store import Store

    cfg = _cfg(data_dir=data_dir)
    tpl.configure(cfg.templates_dir)
    t = tpl.all_templates().get(template)
    if t is None:
        _fail(f"unknown template {template!r}; available: {', '.join(tpl.all_templates())}")
        sys.exit(2)
    store = Store(Path(cfg.data_dir) / "codraft.db")
    doc = store.create_document(*new_document(cfg, house_defaults(), template, t, title))
    _echo(doc["id"])


# Every format GET /api/docs/{id}/export serves (the README's CLI list).
EXPORT_FORMATS = ["pdf", "docx", "pptx", "png", "jpg", "html", "gif", "mp4"]


@main.command()
@click.argument("doc_id")
@click.option("-f", "--format", "fmt", type=click.Choice(EXPORT_FORMATS), default="pdf")
@click.option("-o", "--output", type=click.Path(path_type=Path))
@click.option("--url", default=None, help="Server URL (default http://localhost:<port>).")
@click.option("-p", "--port", type=int, help="Port of the running server (default 8060).")
def export(doc_id, fmt, output, url, port) -> None:
    """Export a document through a running Codraft server."""
    import httpx

    from .render import auth_headers

    cfg = _cfg(port=port)
    base = url or f"http://{cfg.host}:{cfg.port}"
    try:
        # The configured password / proxy secret, as the server checks them on loopback too (gh #2).
        r = httpx.get(f"{base}/api/docs/{doc_id}/export", params={"format": fmt},
                      headers=auth_headers(cfg), timeout=180)
    except httpx.HTTPError as exc:
        _fail(f"no Codraft server at {base} ({type(exc).__name__}); start one with "
              "`langstage-codraft run`, or pass --port / --url")
        sys.exit(1)
    if r.status_code != 200:
        _fail(f"{r.status_code}: {r.text[:300]}")
        sys.exit(1)
    disp = r.headers.get("content-disposition", "")
    name = disp.split("filename=")[-1].strip('"') if "filename=" in disp else f"{doc_id}.{fmt}"
    out = output or Path(name)
    if r.headers.get("content-type") == "application/zip" and out.suffix.lower() != ".zip":
        out = out.with_name(out.name + ".zip")  # multi-page images / HTML come zipped
    out.write_bytes(r.content)
    _ok(f"wrote {out} ({len(r.content)} bytes)")


@main.command()
@click.option("--data-dir", type=click.Path(path_type=Path))
@click.option("-p", "--port", type=int, help="Port of a running Codraft server (for view_pages).")
def mcp(data_dir, port) -> None:
    """Serve Codraft documents over MCP on stdio (for Claude Code, Cursor, Claude Desktop...).

    Works on the same data directory as `run`. view_pages needs a running server; with
    one up, prefer its streamable-HTTP endpoint at http://localhost:8060/mcp.
    """
    from . import templates as tpl
    from . import themes
    from .config import codraft_toml_table
    from .mcp_server import build_mcp_server
    from .render import Renderer, auth_headers, set_renderer
    from .store import Store, set_store

    cfg = _cfg(data_dir=data_dir, port=port)
    set_store(Store(Path(cfg.data_dir) / "codraft.db"))
    table = codraft_toml_table()
    themes.configure(table.get("themes") if isinstance(table.get("themes"), dict) else None,
                     table.get("allowed_themes"))
    tpl.configure(cfg.templates_dir)
    host = "127.0.0.1" if cfg.host in ("localhost", "0.0.0.0", "::") else cfg.host
    try:
        import httpx

        httpx.get(f"http://{host}:{cfg.port}/api/health", timeout=1)
        set_renderer(Renderer(f"http://{host}:{cfg.port}", Path(cfg.data_dir) / "renders",
                              headers=auth_headers(cfg)))
    except Exception:  # noqa: BLE001 - no server: everything but view_pages still works
        pass
    from .server import house_defaults

    build_mcp_server(cfg, house_defaults()).run("stdio")


@main.command("install-browser")
def install_browser_cmd() -> None:
    """Download Chromium for export and the agent's view_pages tool."""
    from .render import install_browser

    code = install_browser()
    if code == 0:
        _ok("Chromium installed: export and view_pages are ready")
    else:
        _fail("Chromium install failed; see the output above")
    sys.exit(code)


if __name__ == "__main__":  # pragma: no cover
    main()
