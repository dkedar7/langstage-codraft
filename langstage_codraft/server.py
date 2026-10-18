"""The Codraft server: REST API (the human path), chat streaming and an AG-UI endpoint
(the agent path), export, and the bundled React app.

Both paths share one :class:`Store`. The UI's chat goes through
``langstage_core.agui.iter_event_frames`` with ``thread_id = document id`` (one agent
thread per document). ``POST /agui`` serves the same agent to any AG-UI client.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

from . import fonts, stylematch, themes
from . import templates as tpl
from .config import CodraftConfig, codraft_toml_table
from .render import BrowserMissing, Renderer, get_renderer, set_renderer, zip_images
from .store import Invalid, NotFound, Store, get_store, set_store
from .textutil import extract_text

log = logging.getLogger("langstage_codraft")
STATIC_DIR = Path(__file__).parent / "static"


class AppState:
    def __init__(self, cfg: CodraftConfig, graph: Any, info: dict):
        self.cfg = cfg
        self.graph = graph
        self.info = info
        self.agent: Any = None
        self.running: dict[str, float] = {}
        self.saver_cm: Any = None
        # doc_id -> client_id -> {name, color, block_id, editing, ts}: who is here (in memory).
        self.presence: dict[str, dict[str, dict]] = {}

    def present(self, doc_id: str) -> list[dict]:
        now = time.time()
        people = self.presence.get(doc_id, {})
        for cid in [c for c, p in people.items() if now - p["ts"] > PRESENCE_TTL]:
            people.pop(cid, None)
        return [{"client_id": c, **{k: v for k, v in p.items() if k != "ts"}} for c, p in sorted(people.items())]


PRESENCE_TTL = 15.0


def _json_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(404, str(exc).strip("'\""))
    if isinstance(exc, (Invalid, ValueError)):
        return HTTPException(400, str(exc))
    return HTTPException(500, f"{type(exc).__name__}: {exc}")


# App-chrome design tokens a deployer can set ([ui.colors] / [ui.colors_dark], or ui=...).
UI_COLOR_KEYS = (
    "primary", "primary_light", "primary_dark", "surface", "surface_2", "surface_3", "border",
    "text", "text_secondary", "text_muted", "success", "error", "warning", "panel", "card",
    "accent_soft", "canvas", "header", "header_text",
)
UI_FEATURES = ("chat", "mcp", "doc_list", "theme_toggle", "style_editor")


def resolve_ui(cfg: CodraftConfig, overrides: dict | None = None) -> dict:
    """Everything the browser needs to brand the app: names, logo, fonts, colours,
    starter prompts and feature switches. Config file < CodraftConfig fields < ``overrides``."""
    from .config import ui_toml_table

    t = ui_toml_table()
    ui: dict[str, Any] = {
        "title": cfg.title, "subtitle": cfg.subtitle, "agent_name": cfg.agent_name,
        "welcome_message": cfg.welcome_message, "icon_url": cfg.icon_url, "theme": cfg.theme,
        "embedded": cfg.embedded,
        "font_family": t.get("font_family", ""), "font_url": t.get("font_url", ""),
        "colors": {k: v for k, v in (t.get("colors") or {}).items() if k in UI_COLOR_KEYS},
        "colors_dark": {k: v for k, v in (t.get("colors_dark") or {}).items() if k in UI_COLOR_KEYS},
        "starter_prompts": [str(x) for x in (t.get("starter_prompts") or [])][:8],
        "features": {f: True for f in UI_FEATURES},
        "custom_css": _load_css(cfg.custom_css),
    }
    ui["features"].update({k: bool(v) for k, v in (t.get("features") or {}).items() if k in UI_FEATURES})
    for k, v in (overrides or {}).items():
        if k in ("colors", "colors_dark") and isinstance(v, dict):
            ui[k] = {**ui[k], **{kk: vv for kk, vv in v.items() if kk in UI_COLOR_KEYS}}
        elif k == "features" and isinstance(v, dict):
            ui["features"].update({kk: bool(vv) for kk, vv in v.items() if kk in UI_FEATURES})
        elif k == "custom_css":
            ui[k] = _load_css(v)
        else:
            ui[k] = v
    return ui


def _load_css(value: str) -> str:
    """Inline CSS, or the contents of a .css file path."""
    v = (value or "").strip()
    if v.endswith(".css") and "\n" not in v and "{" not in v:
        p = Path(v).expanduser()
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            log.warning("codraft: custom_css file not found: %s", p)
            return ""
    return v


def create_app(cfg: CodraftConfig, graph: Any = None, info: dict | None = None,
               *, store: Store | None = None, ui: dict | None = None,
               doc_themes: dict[str, dict] | None = None,
               doc_defaults: dict[str, Any] | None = None) -> FastAPI:
    """The Codraft ASGI app. ``ui`` overrides app branding in code (for hosts that embed
    Codraft); ``doc_themes`` adds or overrides document themes (same keys as
    ``[codraft.themes.*]``, which it extends); ``doc_defaults`` are settings every new
    document starts with (a house cover, header or footer; extends ``[codraft.doc_defaults]``).
    To serve it under another FastAPI app, use :func:`mount`."""
    data_dir = Path(cfg.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    store = store or Store(data_dir / "codraft.db")
    set_store(store)
    table = codraft_toml_table()
    toml_themes = table.get("themes") if isinstance(table.get("themes"), dict) else {}
    themes.configure({**toml_themes, **(doc_themes or {})} or None, table.get("allowed_themes"))
    themes.set_saved_provider(store.saved_themes)
    house = _deep(table.get("doc_defaults") if isinstance(table.get("doc_defaults"), dict) else {},
                  doc_defaults or {})
    tpl.configure(cfg.templates_dir)
    st = AppState(cfg, graph, info or _default_info(graph, ui or {}, cfg))
    st.ui = resolve_ui(cfg, ui)
    # The editor calls the agent by its own name (graph.name, e.g. create_agent(name="Lighthouse"))
    # unless the deployer chose one (ui.agent_name).
    if graph_name(graph) and st.ui.get("agent_name") in ("", "Co-author"):
        st.ui["agent_name"] = graph_name(graph)
    shown = st.ui.get("agent_name")
    if shown not in ("", "Co-author") and st.info.get("kind") == "spec" and st.info.get("label") != shown:
        st.info = {**st.info, "label": shown, "spec": st.info.get("label")}  # the status chip: its name

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await _startup()
        try:
            yield
        finally:
            await _shutdown()

    app = FastAPI(title="langstage-codraft", lifespan=lifespan)
    app.state.codraft = st
    # Codraft over MCP at /mcp (streamable HTTP): any agent harness can co-author.
    mcp_server = None
    try:
        from .mcp_server import build_mcp_server

        mcp_server = build_mcp_server()
        mcp_app = mcp_server.streamable_http_app(streamable_http_path="/mcp", host=cfg.host)
        for route in mcp_app.routes:
            app.router.routes.insert(0, route)
    except Exception as exc:  # noqa: BLE001 - MCP is additive; the editor works without it
        log.warning("codraft: MCP endpoint disabled (%s)", exc)
        mcp_server = None
    st.mcp = mcp_server
    app.add_middleware(
        CORSMiddleware, allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.middleware("http")
    async def _agui_thread_guard(request: Request, call_next):
        if request.method == "POST" and request.url.path.rstrip("/") == "/agui":
            raw = await request.body()
            try:
                tid = json.loads(raw or b"{}").get("threadId") or json.loads(raw or b"{}").get("thread_id")
            except ValueError:
                tid = None
            if tid:
                try:
                    store.get_document(tid)
                except NotFound:
                    return JSONResponse({"detail": f"document {tid} not found: the AG-UI threadId must be a "
                                                   "Codraft document id (create one with POST /api/docs)"},
                                        status_code=404)
        return await call_next(request)

    # ------------------------------------------------------------- access
    @app.middleware("http")
    async def _access_guard(request: Request, call_next):
        """Proxy secret and basic auth. The server's own render requests (loopback)
        are always allowed: headless Chromium has no credentials."""
        client = (request.client.host if request.client else "") or ""
        local = client in ("127.0.0.1", "::1", "localhost", "testclient")
        if not local and cfg.proxy_secret:
            if not _secrets_equal(request.headers.get("x-codraft-proxy-secret", ""), cfg.proxy_secret):
                return JSONResponse({"detail": "forbidden"}, status_code=403)
        elif not local and cfg.auth_password:
            if not _basic_ok(request.headers.get("authorization", ""), cfg.auth_username, cfg.auth_password):
                return Response("authentication required", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Codraft"'})
        return await call_next(request)

    def current_user(request: Request) -> str:
        """The signed-in user's name from the host's trusted header, if configured."""
        if not cfg.user_header:
            return ""
        return (request.headers.get(cfg.user_header) or "").strip()[:120]

    st.current_user = current_user

    def human(request: Request, body: dict) -> str:
        """Who made a human change: the host's signed-in user if it says, else the name the
        browser reports for this collaborator, else "user"."""
        return _human_author(_display_name(current_user(request)) or body.get("author"))

    # ------------------------------------------------------------- lifecycle
    async def _startup() -> None:
        if st.graph is not None:
            st.graph = await _durable_checkpointer(st, data_dir)
            from langstage_core.agui import add_agui_endpoint, build_agent

            st.agent = build_agent(st.graph, name="Codraft co-author")
            add_agui_endpoint(app, st.agent, path="/agui", name="Codraft co-author")
        host = "127.0.0.1" if cfg.host in ("localhost", "0.0.0.0", "::") else cfg.host
        set_renderer(Renderer(cfg.renderer_url or f"http://{host}:{cfg.port}", data_dir / "renders", STATIC_DIR))
        if st.mcp is not None:
            from contextlib import AsyncExitStack

            st.exit_stack = AsyncExitStack()
            await st.exit_stack.enter_async_context(st.mcp.session_manager.run())

    async def _shutdown() -> None:
        if getattr(st, "exit_stack", None) is not None:
            try:
                await st.exit_stack.aclose()
            except Exception:  # noqa: BLE001
                pass
        r = get_renderer()
        if r is not None:
            await asyncio.to_thread(r.close)
            set_renderer(None)
        if st.saver_cm is not None:
            try:
                await st.saver_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- config
    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "agent": st.info.get("label"), "kind": st.info.get("kind")}

    @app.get("/api/config")
    def app_config(request: Request) -> dict:
        from . import __version__

        user = current_user(request)
        return {
            "ui": st.ui,
            "user": {"name": _display_name(user), "id": user} if user else None,
            "title": cfg.title, "version": __version__, "agent": st.info,
            "approval_default": cfg.approval, "default_theme": cfg.default_theme,
            "default_page_size": cfg.default_page_size or "letter",
            "themes": themes.all_themes(), "templates": tpl.summaries(),
            "fonts": fonts.catalog(), "web_fonts": cfg.web_fonts,
            "cover_default": bool((house.get("cover") or {}).get("enabled", True)),
            "chat_available": st.graph is not None,
            "ui_theme": getattr(cfg, "theme", "auto"),
            "mcp_url": "/mcp" if (st.mcp is not None and st.ui["features"].get("mcp", True)) else None,
        }

    # ------------------------------------------------------------- documents
    @app.get("/api/docs")
    def list_docs() -> list:
        return store.list_documents()

    @app.post("/api/docs")
    def create_doc(body: dict = Body(default={})) -> dict:
        try:
            template = tpl.all_templates().get(body.get("template") or "blank", tpl.BUILTIN_TEMPLATES["blank"])
            base = _deep({"approval": cfg.approval, "theme": cfg.default_theme}, house)
            if cfg.default_page_size:
                base["page_size"] = cfg.default_page_size
            tpl_settings = dict(template.get("settings", {}))
            if (body.get("template") or "blank") in tpl.BUILTIN_TEMPLATES:
                # A deployer's house style beats the built-ins' generic look ("classic") and
                # their paper size (regional, not a design choice). Templates with a distinct
                # look (academic, bold, modern, minimal) keep their theme.
                if tpl_settings.get("theme") == "classic":
                    tpl_settings.pop("theme")
                if cfg.default_page_size:
                    tpl_settings.pop("page_size", None)
            settings = _deep(_deep(base, tpl_settings), body.get("settings") or {})
            is_blank = body.get("template") in (None, "", "blank")
            title = body.get("title") or ("Untitled document" if is_blank else template.get("name", "Untitled"))
            blocks = [dict(b) for b in template.get("blocks", [])]
            if body.get("title") and blocks and blocks[0].get("type") == "heading":
                blocks[0] = {**blocks[0], "content": {**blocks[0].get("content", {}), "text": body["title"]}}
            doc = store.create_document(title, settings, blocks)
            return store.full(doc["id"])
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.get("/api/docs/{doc_id}")
    def get_doc(doc_id: str) -> dict:
        try:
            return store.full(doc_id)
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.get("/api/docs/{doc_id}/rev")
    def get_rev(doc_id: str) -> dict:
        try:
            return {"rev": store.rev(doc_id), "running": doc_id in st.running}
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.patch("/api/docs/{doc_id}")
    def patch_doc(doc_id: str, body: dict = Body(...)) -> dict:
        try:
            if "settings" in body and "theme" in body["settings"] and body["settings"]["theme"] not in themes.theme_names():
                raise Invalid(f"unknown theme {body['settings']['theme']!r}")
            store.update_document(doc_id, title=body.get("title"), settings=body.get("settings"))
            return store.full(doc_id)
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.delete("/api/docs/{doc_id}")
    def delete_doc(doc_id: str) -> dict:
        try:
            store.delete_document(doc_id)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.post("/api/docs/{doc_id}/duplicate")
    def duplicate_doc(doc_id: str) -> dict:
        try:
            return store.full(store.duplicate_document(doc_id)["id"])
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    # --------------------------------------------------------------- live
    @app.post("/api/docs/{doc_id}/presence")
    def presence(doc_id: str, body: dict = Body(...)) -> dict:
        cid = str(body.get("client_id") or "")[:64]
        if not cid:
            raise HTTPException(400, "client_id required")
        if body.get("leave"):
            st.presence.get(doc_id, {}).pop(cid, None)
        else:
            st.presence.setdefault(doc_id, {})[cid] = {
                "name": str(body.get("name") or "Guest")[:40], "color": str(body.get("color") or "#2563eb")[:16],
                "block_id": body.get("block_id"), "editing": bool(body.get("editing")), "ts": time.time()}
        return {"presence": st.present(doc_id)}

    @app.get("/api/docs/{doc_id}/events")
    async def events(doc_id: str, request: Request, once: bool = False) -> StreamingResponse:
        """Server-sent events: the document revision, who is present, and whether the
        agent is running. Every open tab re-renders as soon as anyone changes anything.
        ``?once=1`` sends a single snapshot and closes (a poll for clients without SSE)."""
        try:
            store.get_document(doc_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc

        async def gen():
            last = None
            idle = 0.0
            while not await request.is_disconnected():
                try:
                    snap = {"rev": store.rev(doc_id), "presence": st.present(doc_id),
                            "running": doc_id in st.running}
                except NotFound:
                    yield "event: deleted\ndata: {}\n\n"
                    return
                if snap != last:
                    last = snap
                    idle = 0.0
                    yield f"data: {json.dumps(snap)}\n\n"
                    if once:
                        return
                elif idle >= 15:
                    idle = 0.0
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.4)
                idle += 0.4

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/docs/{doc_id}/layout")
    def layout_report(doc_id: str, body: dict = Body(...)) -> dict:
        store.set_layout_report(doc_id, body)
        return {"ok": True}

    # ---------------------------------------------------------------- blocks
    @app.post("/api/docs/{doc_id}/blocks")
    def add_block(doc_id: str, request: Request, body: dict = Body(...)) -> dict:
        try:
            return store.insert_block(
                doc_id, body.get("type", "text"), body.get("content"),
                after=body.get("after", "__end__"), layout=body.get("layout"), refs=body.get("refs"),
                author=human(request, body),
            )
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.patch("/api/blocks/{block_id}")
    def patch_block(block_id: str, request: Request, body: dict = Body(...)) -> dict:
        try:
            b = None
            if "content" in body or "refs" in body:
                b = store.edit_block(block_id, content=body.get("content"), refs=body.get("refs"),
                                     author=human(request, body),
                                     note=body.get("note", "edited"),
                                     base=body.get("base"))
            if "layout" in body:
                b = store.set_layout(block_id, body["layout"])
            return b or store.get_block(block_id)
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.delete("/api/blocks/{block_id}")
    def delete_block(block_id: str) -> dict:
        try:
            store.delete_block(block_id)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.post("/api/blocks/{block_id}/move")
    def move_block(block_id: str, body: dict = Body(...)) -> dict:
        try:
            return store.move_block(block_id, body.get("after"))
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.post("/api/blocks/{block_id}/duplicate")
    def duplicate_block(block_id: str) -> dict:
        try:
            return store.duplicate_block(block_id)
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.get("/api/blocks/{block_id}/versions")
    def block_versions(block_id: str) -> dict:
        try:
            return store.versions(block_id)
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.post("/api/blocks/{block_id}/checkout")
    def checkout(block_id: str, body: dict = Body(...)) -> dict:
        try:
            if body.get("direction"):
                return store.step(block_id, body["direction"])
            return store.checkout(block_id, body["version_id"])
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    # -------------------------------------------------------------- comments
    @app.post("/api/docs/{doc_id}/comments")
    def add_comment(doc_id: str, request: Request, body: dict = Body(...)) -> dict:
        try:
            return store.add_comment(doc_id, body.get("body", ""), block_id=body.get("block_id"),
                                     author=human(request, body),
                                     for_agent=bool(body.get("for_agent", True)))
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.patch("/api/comments/{comment_id}")
    def patch_comment(comment_id: str, request: Request, body: dict = Body(...)) -> dict:
        try:
            if body.get("reply") is not None:
                return store.reply_comment(comment_id, body["reply"], author=human(request, body),
                                           resolve=bool(body.get("resolve")))
            return store.set_comment_status(comment_id, body.get("status", "resolved"))
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.delete("/api/comments/{comment_id}")
    def delete_comment(comment_id: str) -> dict:
        try:
            store.delete_comment(comment_id)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    # ----------------------------------------------------------- suggestions
    @app.post("/api/suggestions/{sid}/accept")
    def accept(sid: str, body: dict = Body(default={})) -> dict:
        try:
            return store.accept_suggestion(sid, payload_override=body.get("payload"))
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.post("/api/suggestions/{sid}/reject")
    def reject(sid: str, body: dict = Body(default={})) -> dict:
        try:
            return store.reject_suggestion(sid, body.get("reason", ""))
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.post("/api/docs/{doc_id}/suggestions/{action}")
    def bulk(doc_id: str, action: str) -> dict:
        if action not in ("accept-all", "reject-all"):
            raise HTTPException(404, "unknown action")
        n = 0
        for s in store.list_suggestions(doc_id):
            try:
                (store.accept_suggestion if action == "accept-all" else store.reject_suggestion)(s["id"])
                n += 1
            except (Invalid, NotFound):
                continue
        return {"ok": True, "count": n}

    # ---------------------------------------------------------------- uploads
    @app.post("/api/docs/{doc_id}/uploads")
    async def upload(doc_id: str, file: UploadFile = File(...), purpose: str = Form("reference")) -> dict:
        data = await file.read()
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(413, "file too large (25 MB max)")
        mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
        text = "" if mime.startswith("image/") else await asyncio.to_thread(extract_text, file.filename or "", data)
        try:
            u = store.add_upload(doc_id, file.filename or "file", data, purpose=purpose, mime=mime, text=text)
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc
        # PDFs get their first pages rendered, so the agent can see them; a "style" upload
        # also gets its look read out (fonts, colours, page setup, cover).
        meta = await asyncio.to_thread(_describe_upload, u, data, purpose == "style")
        return store.set_upload_meta(u["id"], meta) if meta else u

    @app.post("/api/uploads/{uid}/apply-style")
    def apply_style(uid: str) -> dict:
        """Apply the look read from a "style" upload to its document."""
        try:
            u = store.get_upload(uid)
            if not u.get("style"):
                raise Invalid(f"{u['filename']} has no style to apply (upload it as 'style': a PDF, Word file or page image)")
            if u["style"].get("error"):
                raise Invalid(f"could not read the style of {u['filename']}: {u['style']['error']}")
            patch, changes = stylematch.to_settings(u["style"])
            store.update_document(u["doc_id"], settings=patch)
            return {"doc": store.full(u["doc_id"]), "changes": changes}
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    # ----------------------------------------------------------------- themes
    @app.post("/api/themes")
    def save_theme(request: Request, body: dict = Body(...)) -> dict:
        """Save a look as a theme: ``{label, doc_id}`` saves that document's theme with its
        overrides and cover style; ``{label, values}`` saves explicit theme values."""
        try:
            values = dict(body.get("values") or {})
            if body.get("doc_id"):
                s = store.get_document(body["doc_id"])["settings"]
                values = {k: v for k, v in themes.effective_theme(s).items() if k in themes.THEME_KEYS}
                cover = s.get("cover") or {}
                for k in ("background", "text", "image", "align"):
                    if cover.get(k):
                        values[f"cover_{k}"] = cover[k]
            name = store.save_theme(body.get("label", ""), values, author=current_user(request))
            return {"name": name, "themes": themes.all_themes()}
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.delete("/api/themes/{name}")
    def delete_theme(name: str) -> dict:
        try:
            store.delete_theme(name)
            return {"ok": True, "themes": themes.all_themes()}
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    @app.get("/api/uploads/{uid}/file")
    def upload_file(uid: str) -> FileResponse:
        try:
            u = store.get_upload(uid)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        # Uploads are untrusted (SVG can carry script): never let them run as a page.
        return FileResponse(u["path"], media_type=u["mime"], filename=u["filename"],
                            content_disposition_type="inline",
                            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
                                     "X-Content-Type-Options": "nosniff"})

    @app.delete("/api/uploads/{uid}")
    def delete_upload(uid: str) -> dict:
        try:
            store.delete_upload(uid)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            raise _json_error(exc) from exc

    # ------------------------------------------------------------------- chat
    @app.get("/api/docs/{doc_id}/messages")
    async def messages(doc_id: str) -> list:
        if st.graph is None:
            return []
        try:
            state = await st.graph.aget_state({"configurable": {"thread_id": doc_id}})
        except Exception:  # noqa: BLE001
            return []
        return _history(getattr(state, "values", {}) or {})

    @app.post("/api/docs/{doc_id}/chat")
    async def chat(doc_id: str, body: dict = Body(...)) -> StreamingResponse:
        if st.agent is None:
            raise HTTPException(409, st.info.get("hint") or "no agent is configured")
        try:
            store.get_document(doc_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        message = (body.get("message") or "").strip()
        if not message:
            raise HTTPException(400, "empty message")
        if doc_id in st.running:
            who = st.ui.get("agent_name") if st.ui.get("agent_name") not in ("", "Co-author") else "the agent"
            raise HTTPException(409, f"{who} is already working on this document")
        from langstage_core.agui import iter_event_frames

        async def gen():
            st.running[doc_id] = time.time()
            try:
                async for frame in iter_event_frames(st.agent, message, doc_id, max_result_len=1500):
                    yield f"data: {json.dumps(frame, default=str)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': f'{type(exc).__name__}: {exc}'})}\n\n"
            finally:
                st.running.pop(doc_id, None)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ----------------------------------------------------------------- export
    @app.get("/api/docs/{doc_id}/export")
    async def export(doc_id: str, format: str = "pdf") -> Response:
        try:
            doc = store.get_document(doc_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        r = get_renderer()
        if r is None:
            raise HTTPException(503, "renderer not running")
        stem = _slug(doc["title"])
        fmt = format.lower()
        try:
            if fmt == "pdf":
                data = await r.acall(r.pdf(doc_id))
                return Response(data, media_type="application/pdf",
                                headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'})
            if fmt in ("png", "jpg", "jpeg"):
                ext = "jpg" if fmt != "png" else "png"
                imgs = await r.acall(r.page_images(doc_id, fmt=ext))
                if len(imgs) == 1:
                    return Response(imgs[0][1], media_type=f"image/{'jpeg' if ext == 'jpg' else 'png'}",
                                    headers={"Content-Disposition": f'attachment; filename="{stem}.{ext}"'})
                return Response(zip_images(imgs, ext, stem), media_type="application/zip",
                                headers={"Content-Disposition": f'attachment; filename="{stem}-{ext}.zip"'})
            if fmt in ("docx", "pptx"):
                from . import exporters

                struct = await r.acall(r.structure(doc_id))
                if fmt == "pptx":
                    struct["theme"] = themes.effective_theme(doc["settings"])
                    data = await asyncio.to_thread(exporters.to_pptx, store, doc_id, struct)
                    mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                else:
                    pictures: dict[str, bytes] = {}
                    for p in struct["pages"]:
                        for it in p["items"]:
                            if it.get("png") and not it["fragment"] and it["id"] not in pictures:
                                pictures[it["id"]] = it["png"]
                    data = await asyncio.to_thread(exporters.to_docx, store, doc_id, pictures,
                                                   _references(store, doc_id))
                    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                return Response(data, media_type=mime,
                                headers={"Content-Disposition": f'attachment; filename="{stem}.{fmt}"'})
            if fmt in ("gif", "mp4"):
                from . import exporters

                imgs = await r.acall(r.page_images(doc_id, fmt="png", scale=1.0))
                pages = [b for _, b in imgs]
                if fmt == "gif":
                    data = await asyncio.to_thread(exporters.to_gif, pages)
                    mime = "image/gif"
                else:
                    try:
                        data = await asyncio.to_thread(exporters.to_mp4, pages)
                    except RuntimeError as exc:
                        raise HTTPException(501, str(exc)) from exc
                    mime = "video/mp4"
                return Response(data, media_type=mime,
                                headers={"Content-Disposition": f'attachment; filename="{stem}.{fmt}"'})
            if fmt in ("html", "zip"):
                data = await r.acall(r.html_zip(doc_id, doc["title"]))
                return Response(data, media_type="application/zip",
                                headers={"Content-Disposition": f'attachment; filename="{stem}-html.zip"'})
        except BrowserMissing as exc:
            raise HTTPException(503, str(exc)) from exc
        raise HTTPException(400, "format must be pdf, png, jpg, html, docx, pptx, gif or mp4")

    @app.get("/api/render/status")
    async def render_status() -> dict:
        r = get_renderer()
        ok = bool(r) and await r.acall(r.available())
        return {"available": ok, "hint": None if ok else "Run: langstage-codraft install-browser"}

    # ------------------------------------------------------------------- SPA
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request) -> Response:
        if path.startswith(("api/", "agui", "mcp")):
            raise HTTPException(404)
        f = STATIC_DIR / path
        if path and f.is_file() and STATIC_DIR in f.resolve().parents:
            return FileResponse(f)
        index = STATIC_DIR / "index.html"
        if index.is_file():
            return HTMLResponse(_index_html(index, _public_base(request, cfg)), headers={"Cache-Control": "no-cache"})
        return HTMLResponse(
            "<h1>langstage-codraft</h1><p>The web UI is not built. Run "
            "<code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code>.</p>", status_code=200)

    return app


def _describe_upload(u: dict, data: bytes, want_style: bool) -> dict:
    """Rendered pages (PDFs) and, for a style upload, the style profile."""
    meta: dict[str, Any] = {}
    store = get_store()
    is_pdf = u["mime"] == "application/pdf" or u["filename"].lower().endswith(".pdf")
    if is_pdf:
        try:
            meta["pages"] = stylematch.render_pages(data, store.uploads_dir, u["id"])
        except Exception as exc:  # noqa: BLE001 - the text is still there for the agent
            log.warning("codraft: could not render %s (%s)", u["filename"], exc)
    if want_style:
        images = [u["path"]] if u["mime"].startswith("image/") else []
        meta["style"] = stylematch.analyze(u["filename"], data, u["mime"], images)
    return meta


# Names LangGraph and its builders give a graph by default (and Codraft's own built-in
# agents' trace names): not a name worth showing.
_GENERIC_GRAPH_NAMES = {"langgraph", "agent", "graph", "deepagent", "deep agent", "codraft", "codraft-demo", ""}


def graph_name(graph: Any) -> str:
    """The agent's own name (``graph.name``), or "" when it is missing or a default."""
    n = getattr(graph, "name", None)
    n = n.strip() if isinstance(n, str) else ""
    return "" if n.lower() in _GENERIC_GRAPH_NAMES else n


def _default_info(graph: Any, ui: dict, cfg: CodraftConfig) -> dict:
    """Agent info for a host that passes a graph but no ``info`` (e.g. when mounting)."""
    if graph is None:
        return {"kind": "none", "label": "No agent", "missing_tools": [],
                "hint": "Pass an agent graph to create_app to turn on chat."}
    from .agent import missing_tools

    name = ui.get("agent_name") or (cfg.agent_name if cfg.agent_name != "Co-author" else "") or graph_name(graph) or "Agent"
    try:
        missing = missing_tools(graph)
    except Exception:  # noqa: BLE001
        missing = []
    return {"kind": "spec", "label": name, "missing_tools": missing}


def _secrets_equal(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode(), b.encode())


def _basic_ok(header: str, user: str, password: str) -> bool:
    import base64

    if not header.lower().startswith("basic "):
        return False
    try:
        u, _, p = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:  # noqa: BLE001
        return False
    return _secrets_equal(u, user) and _secrets_equal(p, password)


def _display_name(user: str) -> str:
    """'dana.rivera@example.org' -> 'Dana Rivera'; other values pass through."""
    if "@" in user:
        local = user.split("@", 1)[0]
        return " ".join(w.capitalize() for w in re.split(r"[._+-]+", local) if w) or user
    return user


def _public_base(request: Request, cfg: CodraftConfig) -> str:
    """The URL prefix the browser uses: a trusted proxy's X-Forwarded-Prefix (only with the
    proxy secret); else the mount path for requests from this machine (the export renderer);
    else ``base_path``, then the mount path."""
    root = (request.scope.get("root_path") or "").rstrip("/")
    fwd = request.headers.get("x-forwarded-prefix", "")
    if fwd and cfg.proxy_secret and _secrets_equal(request.headers.get("x-codraft-proxy-secret", ""), cfg.proxy_secret):
        return fwd.rstrip("/")
    client = (request.client.host if request.client else "") or ""
    if client in ("127.0.0.1", "::1"):
        return root
    return (cfg.base_path or root).rstrip("/")


def _index_html(index: Path, base: str) -> str:
    html = index.read_text(encoding="utf-8")
    inject = (f'<base href="{_attr(base)}/"><script>window.__CODRAFT__={json.dumps({"base": base})};</script>')
    return html.replace("<head>", "<head>" + inject, 1)


def _attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def mount(host: FastAPI, path: str, app: FastAPI) -> FastAPI:
    """Serve a Codraft app under ``path`` inside another FastAPI app, running Codraft's
    startup and shutdown inside the host's lifespan (Starlette doesn't run a mounted
    app's lifespan on its own). Call before the host starts."""
    from contextlib import asynccontextmanager as _acm

    host.mount(path, app)
    inner = host.router.lifespan_context

    @_acm
    async def wrapped(a: Any):
        async with app.router.lifespan_context(app):
            async with inner(a) as state:
                yield state

    host.router.lifespan_context = wrapped
    return host


async def _durable_checkpointer(st: AppState, data_dir: Path) -> Any:
    """Give the agent a SQLite checkpointer so each document's conversation survives
    restarts. A checkpointer the deployer compiled in is kept."""
    g = st.graph
    if getattr(g, "checkpointer", None) not in (None, False):
        return g
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        st.saver_cm = AsyncSqliteSaver.from_conn_string(str(data_dir / "checkpoints.db"))
        saver = await st.saver_cm.__aenter__()
        return g.copy(update={"checkpointer": saver})
    except Exception as exc:  # noqa: BLE001
        log.warning("codraft: durable checkpointer unavailable (%s); using in-memory", exc)
        from langgraph.checkpoint.memory import InMemorySaver

        return g.copy(update={"checkpointer": InMemorySaver()})


def _history(values: dict) -> list[dict]:
    out = []
    for m in values.get("messages", []) or []:
        t = getattr(m, "type", "")
        content = m.content if isinstance(m.content, str) else " ".join(
            p.get("text", "") for p in m.content if isinstance(p, dict) and p.get("type") == "text")
        if t == "human":
            if content.startswith(("[Rendered pages", "[Images from your last", "[Images returned by")):
                continue
            out.append({"role": "user", "content": content, "id": m.id})
        elif t == "ai":
            calls = [{"id": c.get("id"), "name": c.get("name"), "args": c.get("args")}
                     for c in (getattr(m, "tool_calls", None) or [])]
            if content or calls:
                out.append({"role": "assistant", "content": content, "tool_calls": calls, "id": m.id})
        elif t == "tool":
            out.append({"role": "tool", "name": m.name, "content": content[:1500],
                        "tool_call_id": getattr(m, "tool_call_id", None), "id": m.id})
    return out


def _human_author(name: object) -> str:
    """A collaborator's display name for a human edit; "agent" is reserved for the agent."""
    n = str(name or "").strip()[:40]
    return "user" if not n or n.lower() in ("agent", "template", "auto") else n


def _references(store: Store, doc_id: str) -> list[dict]:
    seen: set[str] = set()
    out = []
    for b in store.list_blocks(doc_id):
        for r in b["refs"]:
            key = (r.get("url") or r["title"]).lower()
            if key not in seen:
                seen.add(key)
                out.append(r)
    return out


def _deep(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _deep(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _slug(s: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()[:60] or "document"
