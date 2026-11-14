"""Render a data directory without a running server (gh #44).

``langstage-codraft export --data-dir``, the stdio MCP server's ``view_pages`` and
:func:`export_document` all need the renderer, and the renderer needs the app: it loads
the app's own ``/print/<doc>`` route in headless Chromium. So instead of a second render
path, :class:`Standalone` starts the same app (``create_app``) in this process on an
ephemeral port, renders through the same ``/api/docs/<id>/export`` route a browser
download uses, and shuts it down again. What comes out is what a live server would export.

The server is private to this process:

* it listens on ``127.0.0.1`` only, on a port the OS picks (the socket is bound here and
  handed to uvicorn, so the port can't be taken in between);
* every request needs a one-off password made for this run, loopback requests included,
  so another local process can't read or edit documents through it;
* it has no agent and no MCP endpoint.
"""
from __future__ import annotations

import atexit
import copy
import secrets
import socket
import threading
from pathlib import Path
from typing import Any

from .config import CodraftConfig

EXPORT_FORMATS = ("pdf", "docx", "pptx", "png", "jpg", "html", "gif", "mp4")


class ExportError(RuntimeError):
    """An export the server refused. ``status`` is its HTTP status (404: no such document;
    503: Chromium isn't installed; 501: MP4 needs the ``[video]`` extra)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class NoData(RuntimeError):
    """The data directory holds no Codraft documents (no ``codraft.db``)."""


def _detail(response: Any) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    return str(body.get("detail", body)) if isinstance(body, dict) else str(body)


def export_filename(response: Any, doc_id: str, fmt: str) -> str:
    """The file name the server suggests (``<title>.pdf``, ``<title>-png.zip``)."""
    disp = response.headers.get("content-disposition", "")
    return disp.split("filename=")[-1].strip('"') if "filename=" in disp else f"{doc_id}.{fmt}"


def output_path(response: Any, out: Path) -> Path:
    """``out``, with ``.zip`` added when the export came zipped (multi-page images, HTML)."""
    if response.headers.get("content-type") == "application/zip" and out.suffix.lower() != ".zip":
        return out.with_name(out.name + ".zip")
    return out


class Standalone:
    """The Codraft app for one data directory, in this process, for rendering only.

    ::

        with Standalone(".codraft") as codraft:
            pdf = codraft.export("doc_6e07ed1cea", "pdf")
            png = codraft.export("doc_6e07ed1cea", "png")   # same Chromium, already running

    ``data_dir`` overrides the configured one; everything else (themes, ``doc_defaults``,
    web fonts, custom page sizes) comes from the usual ``langstage.toml`` / env chain, or
    from ``cfg``. Starting it sets the process-wide store and renderer, as ``create_app``
    does, and :meth:`close` puts back what was there. In a process that already serves
    Codraft, export through that app's own ``/api/docs/<id>/export`` instead.
    """

    def __init__(self, data_dir: str | Path | None = None, *, cfg: CodraftConfig | None = None,
                 store: Any = None):
        base = cfg if cfg is not None else CodraftConfig.resolve(
            overrides={"data_dir": Path(data_dir)} if data_dir is not None else {})
        self.cfg = copy.copy(base)
        if data_dir is not None:
            self.cfg.data_dir = Path(data_dir)
        self._store = store
        self._owns_store = False
        self._prev_store: Any = None
        self._prev_saved: Any = None
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self.base_url = ""
        self.headers: dict[str, str] = {}

    # ------------------------------------------------------------ lifecycle
    def start(self) -> Standalone:
        if self._server is not None:
            return self
        import uvicorn

        from . import store as store_mod
        from .render import auth_headers
        from .server import create_app

        data_dir = Path(self.cfg.data_dir)
        if self._store is None and not (data_dir / "codraft.db").is_file():
            raise NoData(f"no Codraft documents in {data_dir.resolve()} (no codraft.db there)")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))  # loopback only; the OS picks the port
        port = sock.getsockname()[1]
        cfg = self.cfg
        cfg.host, cfg.port = "127.0.0.1", port
        cfg.renderer_url = ""            # the renderer talks to this server, not a deployer's proxy
        cfg.base_path = ""
        cfg.proxy_secret = ""            # it exempts loopback, so it would protect nothing here
        cfg.auth_username = "codraft-standalone"
        cfg.auth_password = secrets.token_urlsafe(32)  # one-off: every request needs it
        self.headers = auth_headers(cfg)
        self.base_url = f"http://127.0.0.1:{port}"

        from . import themes

        self._prev_store = store_mod._STORE
        self._prev_saved = themes._saved  # create_app points saved themes at its store
        self._owns_store = self._store is None
        try:
            app = create_app(cfg, None, {"kind": "none", "label": "standalone renderer", "missing_tools": []},
                             store=self._store, ui={"features": {"mcp": False}},
                             host_guard=True)
            if self._owns_store:
                self._store = store_mod.get_store()
            # log_config=None and no access log: nothing may reach stdout, which is the
            # protocol channel of the stdio MCP server this can run inside.
            self._server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False,
                                                         log_level="warning", lifespan="on"))
            self._sock = sock
            self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [sock]},
                                            name="codraft-standalone", daemon=True)
            self._thread.start()
            for _ in range(600):  # up to 30 s
                if self._server.started:
                    break
                if not self._thread.is_alive():
                    raise RuntimeError("the in-process Codraft renderer failed to start")
                threading.Event().wait(0.05)
            else:
                raise RuntimeError("the in-process Codraft renderer did not start in 30 s")
        except BaseException:
            if self._server is not None:
                self._server.should_exit = True
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=10)
            self._teardown(sock)
            raise
        return self

    def close(self) -> None:
        if self._server is None:
            return
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=30)
        self._teardown(self._sock)

    def _teardown(self, sock: socket.socket | None) -> None:
        from . import store as store_mod

        self._server = None
        self._thread = None
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        from . import themes

        # create_app pointed saved themes at this app's store; put back what was there, so
        # nothing reads them from a store that is about to be closed.
        if self._store is None or getattr(themes._saved, "__self__", None) is self._store:
            themes.set_saved_provider(self._prev_saved)
        if self._owns_store:
            mine = self._store
            if store_mod._STORE is mine or mine is None:
                store_mod.set_store(self._prev_store)
            if mine is not None:
                try:
                    mine.close()
                except Exception:  # noqa: BLE001
                    pass
            self._store = None
            self._owns_store = False

    def __enter__(self) -> Standalone:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --------------------------------------------------------------- output
    def get(self, doc_id: str, fmt: str = "pdf") -> Any:
        """The raw response of the app's export route (status, headers, bytes)."""
        import httpx

        self.start()
        return httpx.get(f"{self.base_url}/api/docs/{doc_id}/export", params={"format": fmt},
                         headers=self.headers, timeout=600)

    def export(self, doc_id: str, fmt: str = "pdf") -> bytes:
        """The exported bytes. PDF, DOCX, PPTX, GIF and MP4 are the file itself. PNG and JPG
        are the image when the document has one page and a zip of page images otherwise;
        HTML is always a zip (``index.html`` plus assets)."""
        r = self.get(doc_id, fmt)
        if r.status_code != 200:
            raise ExportError(r.status_code, _detail(r))
        return r.content

    def renderer(self) -> Any:
        """The app's renderer (for ``view_pages``-style screenshots)."""
        from .render import get_renderer

        self.start()
        return get_renderer()


def export_document(data_dir: str | Path | None, doc_id: str, fmt: str = "pdf",
                    output: str | Path | None = None, *, cfg: CodraftConfig | None = None) -> bytes | Path:
    """Export one document straight from a data directory; no server needs to be running.

    ::

        from langstage_codraft import export_document

        pdf_bytes = export_document(".codraft", "doc_6e07ed1cea", "pdf")
        path = export_document(".codraft", "doc_6e07ed1cea", "docx", "report.docx")

    ``fmt`` is one of pdf, docx, pptx, png, jpg, html, gif, mp4. With ``output`` the file is
    written and its path returned (``.zip`` is added when the export is a zip: multi-page
    PNG/JPG, HTML); without it the bytes are returned. ``data_dir=None`` uses the configured
    one. It renders with the same app, print route and exporters as a running server, so
    the result is the same. For several exports use :class:`Standalone` as a context
    manager, which keeps one Chromium for all of them.

    Raises :class:`ExportError` (unknown document, Chromium not installed, ...) or
    :class:`NoData` (no ``codraft.db`` in the directory).
    """
    with Standalone(data_dir, cfg=cfg) as codraft:
        r = codraft.get(doc_id, fmt)
        if r.status_code != 200:
            raise ExportError(r.status_code, _detail(r))
        if output is None:
            return r.content
        out = output_path(r, Path(output))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
        return out


# ------------------------------------------------------------- lazy renderer
_lazy_lock = threading.Lock()
_lazy: Standalone | None = None


def lazy_renderer(cfg: CodraftConfig) -> Any:
    """A factory for ``render.set_renderer_factory``: on first use it starts the in-process
    app over the store this process already has open (the stdio MCP server's) and returns
    its renderer. The app lives until the process exits."""

    def factory() -> Any:
        global _lazy
        from .store import get_store

        with _lazy_lock:
            if _lazy is None:
                sa = Standalone(cfg=cfg, store=get_store())
                sa.start()
                atexit.register(sa.close)
                _lazy = sa
            return _lazy.renderer()

    return factory
