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


class RenderFailed(RuntimeError):
    """The print view threw before it finished laying out the document."""


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

    async def _open(self, doc_id: str, *, scale: float = 1.0, preview: bool = False):
        browser = await self._browser_ready()
        ctx = await browser.new_context(device_scale_factor=scale, viewport={"width": 1400, "height": 1000})
        if self.headers:
            async def _authed(route):
                await route.continue_(headers={**route.request.headers, **self.headers})

            await ctx.route(lambda url: url == self._origin or url.startswith(self._origin + "/"), _authed)
        page = await ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            # preview: the same print view, fed the document with its pending suggestions
            # applied (GET /api/docs/<id>?preview=pending). One render path either way.
            query = "?preview=pending" if preview else ""
            await page.goto(f"{self.base_url}/print/{doc_id}{query}", wait_until="networkidle")
            # Wait for the layout, but give up a few seconds after the page throws instead of
            # sitting out the full 30 s (gh #34).
            deadline = time.monotonic() + 30
            failed_at: float | None = None
            while not await page.evaluate(READY_JS):
                now = time.monotonic()
                if errors and failed_at is None:
                    failed_at = now
                if failed_at is not None and now - failed_at > 3:
                    raise RenderFailed(f"the print view of {doc_id} failed: {errors[0]}")
                if now > deadline:
                    raise RenderFailed(f"the print view of {doc_id} did not finish laying out in 30 s")
                await asyncio.sleep(0.1)
        except BaseException:
            await ctx.close()
            raise
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
                               max_pages: int = 4, preview: bool = False) -> list[dict]:
        """JPEG screenshots for the agent's eyes, saved under out_dir/vision. Each shot
        carries ``total`` (pages in the document), ``more`` (pages asked for but cut by
        ``max_pages``) and ``blocks`` (the block ids on that page). ``preview`` renders
        the document with its pending suggestions applied."""
        ctx, page = await self._open(doc_id, scale=1.0, preview=preview)
        try:
            els = await page.query_selector_all(".cd-page")
            total = len(els)
            asked = [p for p in (pages or range(1, total + 1)) if 1 <= p <= total]
            wanted = asked[:max_pages]
            on_page = await page.evaluate(
                "() => Array.from(document.querySelectorAll('.cd-page')).map((pg) => Array.from(new Set("
                "Array.from(pg.querySelectorAll('.cd-item[data-block-id]')).map((n) => n.dataset.blockId))))")
            d = self.out_dir / "vision" / doc_id
            d.mkdir(parents=True, exist_ok=True)
            shots = []
            stamp = int(time.time() * 1000)
            for p, data in await _page_shots(page, 1.0, wanted, jpeg=True, quality=70):
                path = d / f"{stamp}-p{p}.jpg"
                path.write_bytes(data)
                shots.append({"page": p, "path": str(path), "total": total, "more": asked[max_pages:],
                              "blocks": on_page[p - 1] if p - 1 < len(on_page) else []})
            return shots
        finally:
            await ctx.close()

    async def figure_shot(self, doc_id: str, block_ref: str, *, preview: bool = False,
                          svg: str | None = None) -> dict | None:
        """One block as it sits on the page, for the agent's eyes: a 2x screenshot of the
        block (frame, title and caption included), saved under out_dir/vision. It comes from
        the same print view as ``view_pages`` and the exports. ``block_ref`` is a block id,
        or a pending insert's suggestion id with ``preview``. With ``svg`` (the figure's
        source) the result also carries ``issues``: label problems measured in the browser at
        the size the figure has on the page (see ``FIGURE_CHECK_JS``). None when the block
        isn't on any page."""
        ctx, page = await self._open(doc_id, scale=2.0, preview=preview)
        try:
            el = await page.query_selector(f'.cd-item[data-block-id="{block_ref}"]')
            if el is None:
                return None
            img = await el.query_selector("img")
            box = await (img or el).bounding_box()
            if not box or box["width"] < 2 or box["height"] < 2:
                return None
            on_page = await el.evaluate(
                "(n) => 1 + Array.from(document.querySelectorAll('.cd-page')).indexOf(n.closest('.cd-page'))")
            d = self.out_dir / "vision" / doc_id
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{int(time.time() * 1000)}-fig-{re.sub(r'[^A-Za-z0-9_]', '', block_ref)}.jpg"
            path.write_bytes(await el.screenshot(type="jpeg", quality=82))
            out = {"path": str(path), "page": on_page, "width": round(box["width"]), "height": round(box["height"]),
                   "issues": []}
            if svg:
                out["issues"] = await _figure_issues(ctx, svg, box["width"], box["height"])
            return out
        finally:
            await ctx.close()

    async def svg_issues(self, svg: str, width: float = 640, height: float = 0) -> list[str]:
        """The label checks alone, for an SVG that isn't on a page (see ``figure_shot``)."""
        browser = await self._browser_ready()
        ctx = await browser.new_context(viewport={"width": 1400, "height": 1000})
        try:
            return await _figure_issues(ctx, svg, width, height)
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
                                 raster: el.dataset.raster === '1',
                                 x: b.left - r.left, y: b.top - r.top, w: b.width, h: b.height,
                                 text: el.innerText.slice(0, 4000) };
                      }) };
                })"""
            )
            page_els = await page.query_selector_all(".cd-page")
            for pi, p in enumerate(info):
                els = await page_els[pi].query_selector_all(".cd-item")
                for it, el in zip(p["items"], els, strict=False):
                    # Editable text where a text box can show it; a picture for everything
                    # else, and for a block with a fill (a band), which a text box can't draw.
                    if it["fragment"] or it.get("raster") or it["type"] not in ("text", "heading", "callout"):
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

            # With the renderer's own credentials: a password-protected server (and the standalone
            # renderer, gh #44) answers 401 without them and the snapshot lost its assets.
            async with httpx.AsyncClient(base_url=self.base_url, timeout=30, headers=self.headers) as client:
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


# Label problems in a drawn figure, measured on the rendered SVG at the size it has on the
# page: text cut off by the canvas, text too small to read, labels that overlap each other,
# a label wider than the box it sits in, lines or arrowheads that run through a label, and
# one line drawn along another.
# Returns short sentences for the agent; the first eight are enough to act on.
FIGURE_CHECK_JS = r"""() => {
  const svg = document.querySelector('svg');
  if (!svg) return [];
  const R = svg.getBoundingClientRect();
  const out = [];
  const name = (t) => '"' + t.textContent.trim().replace(/\s+/g, ' ').slice(0, 40) + '"';
  const shown = (e) => { const cs = getComputedStyle(e);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity) > 0.05; };
  const inDefs = (e) => !!e.closest('defs, marker, clipPath, mask, pattern, symbol');
  const texts = [...svg.querySelectorAll('text')].filter((t) => t.textContent.trim() && !inDefs(t) && shown(t))
    .map((t) => ({ el: t, r: t.getBoundingClientRect(), name: name(t) })).filter((t) => t.r.width > 0);
  const px = (v) => Math.round(v);
  // Positions are reported in viewBox units, the numbers the SVG was written in.
  const sm = svg.getScreenCTM();
  const inv = sm ? sm.inverse() : null;
  const toUser = (x, y) => (inv ? { x: inv.a * x + inv.c * y + inv.e, y: inv.b * x + inv.d * y + inv.f } : { x, y });
  const pt = (p) => { const u = toUser(p.x, p.y); return `(${px(u.x)}, ${px(u.y)})`; };
  const span = (r) => { const a = toUser(r.left, r.top), b = toUser(r.right, r.bottom);
    return `x ${px(a.x)}-${px(b.x)}, y ${px(a.y)}-${px(b.y)}`; };
  const hex = (c) => { const m = /rgba?\((\d+), (\d+), (\d+)/.exec(c || '');
    return m ? '#' + [m[1], m[2], m[3]].map((v) => Number(v).toString(16).padStart(2, '0')).join('') : (c || ''); };
  // 1. cut off by the canvas
  for (const t of texts) {
    const sides = [['left', R.left - t.r.left], ['right', t.r.right - R.right], ['top', R.top - t.r.top],
                   ['bottom', t.r.bottom - R.bottom]].sort((a, b) => b[1] - a[1]);
    if (sides[0][1] > 1.5)
      out.push(`${t.name} is cut off at the ${sides[0][0]} edge of the figure (by ${px(sides[0][1])} px): move it inside or enlarge the viewBox.`);
  }
  // 2. too small to read at the size the figure prints
  const small = [];
  for (const t of texts) {
    const m = t.el.getScreenCTM();
    const k = m ? Math.hypot(m.a, m.b) : 1;
    const size = parseFloat(getComputedStyle(t.el).fontSize) * k;
    if (size < 6.5) small.push({ t, size });
  }
  if (small.length) {
    const worst = small.reduce((a, b) => (a.size < b.size ? a : b));
    out.push(`${small.length} label(s) print smaller than 6.5 px (e.g. ${worst.t.name} at ${worst.size.toFixed(1)} px): too small to read. `
      + 'Use larger font sizes relative to the viewBox, fewer labels, or make the figure full width.');
  }
  const inter = (a, b) => { const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top); return w > 0 && h > 0 ? w * h : 0; };
  // 3. labels on top of each other
  for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) {
    const a = texts[i], b = texts[j];
    if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
    const area = inter(a.r, b.r);
    if (area > 0.15 * Math.min(a.r.width * a.r.height, b.r.width * b.r.height) && area > 12)
      out.push(`${a.name} (${span(a.r)}) and ${b.name} (${span(b.r)}) overlap each other: move one clear of the other.`);
  }
  // 4. a label wider or taller than the box it sits in
  const shapes = [...svg.querySelectorAll('rect, ellipse, circle')].filter((e) => !inDefs(e) && shown(e)).map((e) => {
    const cs = getComputedStyle(e);
    return { r: e.getBoundingClientRect(), drawn: cs.fill !== 'none' || cs.stroke !== 'none' };
  }).filter((s) => s.drawn && s.r.width > 8 && s.r.height > 8 && s.r.width * s.r.height < 0.6 * R.width * R.height);
  for (const t of texts) {
    const cx = (t.r.left + t.r.right) / 2, cy = (t.r.top + t.r.bottom) / 2;
    let box = null;
    for (const s of shapes) {
      if (cx > s.r.left && cx < s.r.right && cy > s.r.top && cy < s.r.bottom
          && (!box || s.r.width * s.r.height < box.width * box.height)) box = s.r;
    }
    if (!box) continue;
    const wide = Math.max(box.left - t.r.left, t.r.right - box.right);
    const tall = Math.max(box.top - t.r.top, t.r.bottom - box.bottom);
    if (wide > 2) out.push(`${t.name} is wider than the box it sits in (it sticks out by ${px(wide)} px): shorten it, split it into two lines, or widen the box.`);
    else if (tall > 2) out.push(`${t.name} is taller than the box it sits in (by ${px(tall)} px): enlarge the box or use a smaller label.`);
  }
  // 5. lines and arrowheads through a label
  const hitLine = new Map(), hitHead = new Map(), lines = [];
  const geo = [...svg.querySelectorAll('line, polyline, path')].filter((e) => !inDefs(e) && shown(e));
  for (const g of geo) {
    const cs = getComputedStyle(g);
    if (cs.stroke === 'none' || !g.getTotalLength) continue;
    if (g.tagName === 'path' && cs.fill !== 'none') continue;  // a filled shape, not a connector
    const m = g.getScreenCTM();
    if (!m) continue;
    let len = 0;
    try { len = g.getTotalLength(); } catch (e) { continue; }
    if (!(len > 0)) continue;
    const k = Math.hypot(m.a, m.b);
    const half = (parseFloat(cs.strokeWidth) || 1) * k / 2;
    const at = (d) => { const p = g.getPointAtLength(d); return { x: m.a * p.x + m.c * p.y + m.e, y: m.b * p.x + m.d * p.y + m.f }; };
    const step = Math.max(1.5 / k, len / 400);
    if (len * k > 12) {
      const pts = [];
      for (let i = 0; i <= 40; i++) pts.push(at(len * i / 40));
      lines.push({ pts, half, what: `${hex(cs.stroke)}, from ${pt(at(0))} to ${pt(at(len))}` });
    }
    for (let d = 0; d <= len; d += step) {
      const p = at(d);
      for (const t of texts) {
        if (hitLine.has(t)) continue;
        // A text box is taller than its glyphs: a line has to reach well into it to touch them.
        const inset = t.r.height * 0.2 - half;
        if (p.x > t.r.left + inset && p.x < t.r.right - inset && p.y > t.r.top + inset && p.y < t.r.bottom - inset)
          hitLine.set(t, `${hex(cs.stroke)}, from ${pt(at(0))} to ${pt(at(len))}`);
      }
    }
    for (const [attr, d] of [['marker-end', len], ['marker-start', 0]]) {
      const ref = /url\(["']?#([^"')]+)/.exec(cs.getPropertyValue(attr) || g.getAttribute(attr) || '');
      const mk = ref && svg.querySelector('#' + CSS.escape(ref[1]));
      if (!mk) continue;
      const user = mk.getAttribute('markerUnits') === 'userSpaceOnUse';
      const unit = (user ? 1 : (parseFloat(cs.strokeWidth) || 1)) * k;
      const w = (parseFloat(mk.getAttribute('markerWidth')) || 3) * unit, h = (parseFloat(mk.getAttribute('markerHeight')) || 3) * unit;
      // The head lies along the line: mostly behind the line's end, and ahead of it by
      // whatever part of the marker lies past its reference point (refX). A label just
      // beyond the tip is clear of it.
      const p = at(d), q = at(d ? Math.max(0, len - Math.min(len, 2 / k)) : Math.min(len, 2 / k));
      const n = Math.hypot(p.x - q.x, p.y - q.y) || 1;
      const ux = (p.x - q.x) / n, uy = (p.y - q.y) / n;
      const vb = (mk.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
      const across = vb.length === 4 && vb[2] > 0 ? vb[2] : (parseFloat(mk.getAttribute('markerWidth')) || 3);
      const ahead = Math.min(1, Math.max(0, 1 - (parseFloat(mk.getAttribute('refX')) || 0) / across)) * w;
      const f = { x: p.x + ux * ahead, y: p.y + uy * ahead }, r = { x: p.x - ux * (w - ahead), y: p.y - uy * (w - ahead) };
      const dx = Math.abs(ux), dy = Math.abs(uy);
      const ex = dy > 2 * dx ? h / 2 : dx > 2 * dy ? 1 : h * 0.35, ey = dx > 2 * dy ? h / 2 : dy > 2 * dx ? 1 : h * 0.35;
      const head = { left: Math.min(f.x, r.x) - ex, right: Math.max(f.x, r.x) + ex,
                     top: Math.min(f.y, r.y) - ey, bottom: Math.max(f.y, r.y) + ey };
      const glyphs = (r) => ({ left: r.left, right: r.right, top: r.top + r.height * 0.2, bottom: r.bottom - r.height * 0.2 });
      for (const t of texts) if (!hitLine.has(t) && inter(head, glyphs(t.r)) > 6)
        hitHead.set(t, `at ${pt(p)}, on the ${hex(cs.stroke)} line ${d ? 'from ' + pt(at(0)) : 'to ' + pt(at(len))}`);
    }
  }
  // 6. one connector drawn along another (an arrow on top of an arrow)
  for (let i = 0; i < lines.length; i++) for (let j = i + 1; j < lines.length; j++) {
    const a = lines[i], b = lines[j];
    const near = (a.half + b.half) * 0.75 + 1;
    const on = (p, q) => p.pts.filter((u) => q.pts.some((v) => Math.hypot(u.x - v.x, u.y - v.y) < near)).length / p.pts.length;
    if (Math.max(on(a, b), on(b, a)) > 0.4)
      out.push(`Two lines are drawn on top of each other: (${a.what}) and (${b.what}). Put them side by side, with a gap.`);
  }
  for (const [t, line] of hitLine) out.push(`A line (${line}) runs through ${t.name} (${span(t.r)}): move the label or the line so they are clear of each other.`);
  for (const [t, head] of hitHead) out.push(`An arrowhead (${head}) covers part of ${t.name} (${span(t.r)}): move the label away from the arrow's end.`);
  return out.slice(0, 8);
}"""


async def _figure_issues(ctx: Any, svg: str, width: float, height: float = 0) -> list[str]:
    """Run ``FIGURE_CHECK_JS`` on ``svg`` drawn ``width`` x ``height`` CSS px (the box its
    <img> has on the page; height 0 follows the viewBox). A check that fails to run reports
    nothing: it must never stop the figure from being drawn."""
    page = await ctx.new_page()
    try:
        box = f"width:{width:.1f}px;" + (f"height:{height:.1f}px;" if height else "")
        await page.set_content(
            "<!doctype html><html><body style='margin:0'>"
            f"<div id='f' style='{box}'>{svg}</div>"
            "<style>#f>svg{width:100%;height:100%;display:block}</style></body></html>")
        return [str(x) for x in await page.evaluate(FIGURE_CHECK_JS)]
    except Exception:  # noqa: BLE001
        return []
    finally:
        await page.close()


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


_FACTORY: Any = None


def set_renderer_factory(factory: Any) -> None:
    """How to get a renderer when none is running yet (gh #44): the stdio MCP server sets
    one that starts the app in-process the first time ``view_pages`` is called."""
    global _FACTORY
    _FACTORY = factory


def ensure_renderer() -> Renderer | None:
    """The running renderer, else the one the factory starts; ``None`` if there's neither."""
    r = get_renderer()
    if r is not None or _FACTORY is None:
        return r
    return _FACTORY()
