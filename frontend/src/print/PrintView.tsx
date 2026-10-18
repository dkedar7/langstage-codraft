import { useEffect, useState } from "react";
import { api } from "../api";
import { pageMm } from "../layout/units";
import { BlockView } from "../render/BlockView";
import { pendingCharts } from "../render/Chart";
import { Fragment, Page } from "../render/Page";
import { themeVars } from "../render/theme";
import { langCode, pinHeight, useDocLayout } from "../render/useDocLayout";
import type { AppConfig, Doc } from "../types";

declare global {
  interface Window {
    __codraftReady?: boolean;
  }
}

/** /print/:id — the read-only page renderer that export and view_pages capture. */
export function PrintView({ docId }: { docId: string }) {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    window.__codraftReady = false;
    Promise.all([api.getDoc(docId), api.config()]).then(([d, c]) => {
      setDoc(d);
      setCfg(c);
    }).catch((e) => setErr(String(e.message || e)));
  }, [docId]);

  if (err) return <div style={{ padding: 40 }}>Could not load document: {err}</div>;
  if (!doc || !cfg) return null;
  return <PrintPages doc={doc} cfg={cfg} />;
}

function PrintPages({ doc, cfg }: { doc: Doc; cfg: AppConfig }) {
  const theme = cfg.themes[doc.settings.theme];
  const { layout, geo, ctx, measureNode, ready, measured } = useDocLayout(doc.blocks, doc.settings, theme, false, true);
  const byId = Object.fromEntries(doc.blocks.map((b) => [b.id, b]));
  const start = doc.settings.page_number_start ?? 1;
  const pctx = { ...ctx, title: doc.title, pageCount: start + layout.pages.length - 1 };
  const [w, h] = pageMm(doc.settings);
  const pageless = doc.settings.mode === "pageless";

  useEffect(() => {
    if (!ready) return;
    let alive = true;
    const check = () => {
      if (!alive) return;
      const imgs = Array.from(document.images);
      const imagesDone = imgs.every((i) => i.complete);
      if (pendingCharts.count === 0 && imagesDone) {
        requestAnimationFrame(() => requestAnimationFrame(() => {
          if (!alive) return;
          window.__codraftReady = true;
          if (new URLSearchParams(window.location.search).get("autoprint")) setTimeout(() => window.print(), 150);
        }));
      } else {
        setTimeout(check, 100);
      }
    };
    window.__codraftReady = false;
    const t = setTimeout(check, 50);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [ready, layout, measured]);

  const pageCss = pageless
    ? `@page { size: ${geo.pageW / (96 / 25.4)}mm ${(layout.pages[0]?.height || 0) / (96 / 25.4) + 40}mm; margin: 0 }`
    : `@page { size: ${w}mm ${h}mm; margin: 0 }`;

  return (
    <div id="cd-print-root" className="cd-print">
      <style>{pageCss}</style>
      <div className="cd-doc cd-pages" style={themeVars(theme)} lang={langCode(doc.settings)}>
        {layout.pages.map((p) => (
          <Page key={p.number} page={p} geo={geo} kind={p.kind} renderItem={(it) => {
            const b = byId[it.id];
            if (!b) return null;
            const pageNo = start + p.number - 1;
            return (
              <div key={`${it.id}-${it.offset}`} className="cd-item" data-block-id={it.id} data-type={b.type} data-fragment={it.fragment ? "1" : "0"} style={{ position: "absolute", left: it.x, top: it.y, width: it.w, height: it.h }}>
                <Fragment item={it}>
                  <BlockView block={b} width={it.w} ctx={pctx} fixedHeight={pinHeight(b) || b.layout.height} pageNo={pageNo} />
                </Fragment>
              </div>
            );
          }} />
        ))}
      </div>
      {measureNode}
    </div>
  );
}
