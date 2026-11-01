"""Headless-Chromium renderer: PDF / PNG / JPG / HTML export and ``view_pages``.

What the author sees and what gets exported come from one renderer. The renderer
loads the app's own ``/print/<doc>`` route, which is the same React page renderer and
paginator as the editor, in read-only mode. It waits for the page to signal it is
laid out (fonts loaded, pagination done), then prints or screenshots it. Chromium
never re-paginates: every page is an explicit fixed-size box.

Playwright's async API is bound to the event loop it started on, so the renderer
owns one background thread with its own loop. Sync callers (agent tools in worker
threads) and async callers (FastAPI handlers) both submit coroutines to that loop.
"""
from __future__ import annotations

import asyncio
import io
import json
import math
import re
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

READY_JS = "() => window.__codraftReady === true"

# Every page's box in document CSS px (left, top, width, height).
PAGE_RECTS_JS = """() => [...document.querySelectorAll('.cd-page')].map(e => {
  const r = e.getBoundingClientRect();
  return [r.left + window.scrollX, r.top + window.scrollY, r.width, r.height];
})"""


class BrowserMissing(RuntimeError):
    """Chromium for Playwright isn't installed."""


def install_browser() -> int:
    """``python -m playwright install chromium`` (what ``install-browser`` runs)."""
    return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])


def auth_headers(cfg: Any) -> dict[str, str]:
    """The credentials the renderer's headless Chromium sends to its own server: the
    proxy secret and basic auth, when they're configured (gh #2)."""
    import base64

    headers: dict[str, str] = {}
    if getattr(cfg, "proxy_secret", ""):
        headers["x-codraft-proxy-secret"] = cfg.proxy_secret
    if getattr(cfg, "auth_password", ""):
        token = base64.b64encode(f"{cfg.auth_username}:{cfg.auth_password}".encode()).decode()
        headers["authorization"] = f"Basic {token}"
    return headers


class Renderer:
    def __init__(self, base_url: str, out_dir: Path, static_dir: Path | None = None,
                 headers: dict[str, str] | None = None):
        self.base_url = base_url.rstrip("/")
        # Sent only to the server's own origin, never to a third party (web fonts).
        self.headers = dict(headers or {})
        parts = urlparse(self.base_url)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.static_dir = static_dir
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="codraft-render", daemon=True)
        self._thread.start()
        self._pw = None
        self._browser = None
        self._lock: asyncio.Lock | None = None

    # ------------------------------------------------------------- plumbing
    def call(self, coro, timeout: float = 120) -> Any:
        """Run a renderer coroutine from any thread (blocking)."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def acall(self, coro) -> Any:
        """Run a renderer coroutine from another event loop (awaitable)."""
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._loop))

    async def _browser_ready(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
                try:
                    self._browser = await self._pw.chromium.launch()
                except Exception as exc:
                    msg = str(exc)
                    if "Executable doesn't exist" in msg or "playwright install" in msg:
                        raise BrowserMissing(
                            "Chromium is not installed for export/vision. Run: langstage-codraft install-browser"
                        ) from exc
                    raise
            return self._browser

    async def available(self) -> bool:
        try:
            await self._browser_ready()
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        async def _close():
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()

        try:
            self.call(_close(), timeout=15)
        except Exception:  # noqa: BLE001
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    async def _open(self, doc_id: str, *, scale: float = 1.0):
        browser = await self._browser_ready()
        ctx = await browser.new_context(device_scale_factor=scale, viewport={"width": 1400, "height": 1000})
        if self.headers:
            async def _authed(route):
                await route.continue_(headers={**route.request.headers, **self.headers})

            await ctx.route(lambda url: url == self._origin or url.startswith(self._origin + "/"), _authed)
        page = await ctx.new_page()
        await page.goto(f"{self.base_url}/print/{doc_id}", wait_until="networkidle")
        await page.wait_for_function(READY_JS, timeout=30000)
        return ctx, page

    # --------------------------------------------------------------- outputs
    async def pdf(self, doc_id: str) -> bytes:
        ctx, page = await self._open(doc_id)
        try:
            await page.emulate_media(media="print")
            await page.wait_for_function(READY_JS, timeout=30000)
            return await page.pdf(prefer_css_page_size=True, print_background=True)
        finally:
            await ctx.close()

    async def page_images(self, doc_id: str, *, fmt: str = "png", scale: float = 2.0,
                          pages: list[int] | None = None, quality: int = 85) -> list[tuple[int, bytes]]:
        ctx, page = await self._open(doc_id, scale=scale)
        try:
            jpeg = fmt in ("jpg", "jpeg")
            return await _page_shots(page, scale, pages or None, jpeg=jpeg, quality=quality)
        finally:
            await ctx.close()

    async def page_count(self, doc_id: str) -> int:
        ctx, page = await self._open(doc_id)
        try:
            return len(await page.query_selector_all(".cd-page"))
        finally:
            await ctx.close()

    async def screenshot_pages(self, doc_id: str, *, pages: list[int] | None = None,
                               max_pages: int = 4) -> list[dict]:
        """JPEG screenshots for the agent's eyes, saved under out_dir/vision."""
        ctx, page = await self._open(doc_id, scale=1.0)
        try:
            els = await page.query_selector_all(".cd-page")
            total = len(els)
            wanted = [p for p in (pages or range(1, total + 1)) if 1 <= p <= total][:max_pages]
            d = self.out_dir / "vision" / doc_id
            d.mkdir(parents=True, exist_ok=True)
            shots = []
            stamp = int(time.time() * 1000)
            for p, data in await _page_shots(page, 1.0, wanted, jpeg=True, quality=70):
                path = d / f"{stamp}-p{p}.jpg"
                path.write_bytes(data)
                shots.append({"page": p, "path": str(path), "total": total})
            return shots
        finally:
            await ctx.close()

    async def structure(self, doc_id: str, *, scale: float = 2.0) -> dict:
        """Where every block landed on every page (px, page-relative), with a PNG of each
        item that isn't editable text (pictures for PPTX/DOCX)."""
        ctx, page = await self._open(doc_id, scale=scale)
        try:
            info = await page.evaluate(
                """() => [...document.querySelectorAll('.cd-page')].map(p => {
                    const r = p.getBoundingClientRect();
                    return { w: r.width, h: r.height, kind: p.dataset.kind || 'body',
                      items: [...p.querySelectorAll('.cd-item')].map(el => {
                        const b = el.getBoundingClientRect();
                        return { id: el.dataset.blockId, type: el.dataset.type,
                                 fragment: el.dataset.fragment === '1',
                                 x: b.left - r.left, y: b.top - r.top, w: b.width, h: b.height,
                                 text: el.innerText.slice(0, 4000) };
                      }) };
                })"""
            )
            page_els = await page.query_selector_all(".cd-page")
            for pi, p in enumerate(info):
                els = await page_els[pi].query_selector_all(".cd-item")
                for it, el in zip(p["items"], els, strict=False):
                    if it["fragment"] or it["type"] not in ("text", "heading", "callout"):
                        if it["w"] > 1 and it["h"] > 1:
                            it["png"] = await el.screenshot(type="png")
                p["notes"] = "\n\n".join(i["text"] for i in p["items"] if i.get("text"))
            size = (info[0]["w"], info[0]["h"]) if info else (816, 1056)
            return {"page_px": size, "pages": info}
        finally:
            await ctx.close()

    async def html_zip(self, doc_id: str, title: str = "document") -> bytes:
        """A static, self-contained HTML snapshot: index.html + assets/."""
        ctx, page = await self._open(doc_id)
        try:
            payload = await page.evaluate(
                """() => {
                    const css = [];
                    for (const sheet of document.styleSheets) {
                        try { for (const r of sheet.cssRules) css.push(r.cssText); } catch (e) {}
                    }
                    const root = document.querySelector('#cd-print-root');
                    return { css: css.join('\\n'), html: root ? root.outerHTML : document.body.innerHTML };
                }"""
            )
            html, css = payload["html"], payload["css"]
            assets: dict[str, bytes] = {}
            import httpx

            async with httpx.AsyncClient(base_url=self.base_url, timeout=30) as client:
                async def grab(url: str) -> str:
                    path = urlparse(url).path if url.startswith("http") else url
                    if not path.startswith("/"):
                        return url
                    name = "assets/" + re.sub(r"[^A-Za-z0-9._-]+", "_", path.strip("/"))
                    if name not in assets:
                        try:
                            r = await client.get(path)
                            if r.status_code == 200:
                                assets[name] = r.content
                            else:
                                return url
                        except Exception:  # noqa: BLE001
                            return url
                    return name

                for url in set(re.findall(r'url\((?:"|\')?(/[^)"\']+)(?:"|\')?\)', css)):
                    css = css.replace(url, await grab(url))
                for url in set(re.findall(r'(?:src|href)="(/api/uploads/[^"]+)"', html)):
                    html = html.replace(f'"{url}"', f'"{await grab(url)}"')
            doc = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{_esc(title)}</title><link rel='stylesheet' href='style.css'>"
                "<style>body{margin:0;background:#e9e9ec} .cd-pages{padding:24px 0}"
                "@media print{body{background:none}.cd-pages{padding:0}}</style>"
                f"</head><body>{html}</body></html>"
            )
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("index.html", doc)
                z.writestr("style.css", css)
                for name, data in assets.items():
                    z.writestr(name, data)
                z.writestr("codraft.json", json.dumps({"doc_id": doc_id, "exported_at": time.time()}))
            return buf.getvalue()
        finally:
            await ctx.close()


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _page_shots(page, scale: float, pages: list[int] | None = None, *, jpeg: bool = False,
                      quality: int = 85) -> list[tuple[int, bytes]]:
    """Each wanted page as a PNG/JPEG of exactly that page (gh #32).

    Pages whose height isn't a whole number of CSS px (A4, A5) sit at fractional offsets,
    and an element screenshot rounds its clip outward, so every other page image used to
    start with a row of the page above. Chromium paints a box from the rounded CSS px of
    its top to the rounded CSS px of its bottom, so clip exactly those rows. The layout is
    left alone, so the text lands on the same sub-pixel phase as in the editor. A page that
    paints one row short is padded with its own edge, so every image keeps the full size."""
    from PIL import Image

    def px(v: float) -> int:
        return math.floor(v + 0.5)

    rects = await page.evaluate(PAGE_RECTS_JS)
    out = []
    for i, (x, y, w, h) in enumerate(rects, 1):
        if pages is not None and i not in pages:
            continue
        x0, y0, x1, y1 = px(x), px(y), px(x + w), px(y + h)
        want = (round(math.ceil(w - 1e-6) * scale), round(math.ceil(h - 1e-6) * scale))
        clip = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
        if (round(clip["width"] * scale), round(clip["height"] * scale)) == want:
            kw: dict[str, Any] = {"type": "jpeg", "quality": quality} if jpeg else {"type": "png"}
            out.append((i, await page.screenshot(full_page=True, clip=clip, **kw)))
            continue
        im = _pad_to(Image.open(io.BytesIO(await page.screenshot(full_page=True, clip=clip))).convert("RGB"), want)
        buf = io.BytesIO()
        if jpeg:
            im.save(buf, "JPEG", quality=quality)
        else:
            im.save(buf, "PNG")
        out.append((i, buf.getvalue()))
    return out


def _pad_to(im, size: tuple[int, int]):
    """Crop or extend (repeating the edge row/column, which is page margin) to ``size``."""
    from PIL import Image

    w, h = size
    im = im.crop((0, 0, min(im.width, w), min(im.height, h)))
    if im.size == size:
        return im
    full = Image.new(im.mode, size)
    full.paste(im, (0, 0))
    if im.width < w:
        edge = im.crop((im.width - 1, 0, im.width, im.height)).resize((w - im.width, im.height))
        full.paste(edge, (im.width, 0))
    if im.height < h:
        edge = full.crop((0, im.height - 1, w, im.height)).resize((w, h - im.height))
        full.paste(edge, (0, im.height))
    return full


def zip_images(images: list[tuple[int, bytes]], ext: str, stem: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for page_no, data in images:
            z.writestr(f"{stem}-page-{page_no:02d}.{ext}", data)
    return buf.getvalue()


_RENDERER: Renderer | None = None


def set_renderer(r: Renderer | None) -> None:
    global _RENDERER
    _RENDERER = r


def get_renderer() -> Renderer | None:
    return _RENDERER
