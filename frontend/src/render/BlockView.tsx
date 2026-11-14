import type React from "react";
import { memo, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Block, Ref } from "../types";
import { Chart, DEFAULT_CHART_HEIGHT } from "./Chart";
import { hyphenateText } from "./hyphenate";
import { mdToHtml } from "./markdown";
import { Boundary } from "./Boundary";
import { withBase } from "../base";

export interface RenderCtx {
  /** de-duplicated references in reading order, for `references` blocks */
  references: (Ref & { n: number })[];
  /** editor mode shows placeholders for empty blocks and visible page breaks */
  editing: boolean;
  /** read-only print route (export / view_pages) */
  print?: boolean;
  /** for {pages} / {title} tokens in repeated headers and footers */
  pageCount?: number;
  title?: string;
  /** the agent's name for editor hints ("Lighthouse", or "the agent") */
  agent?: string;
  /** the document's language, for hyphenation (render/hyphenate.ts) */
  lang?: string | null;
  /** hyphenate paragraphs (the document's hyphens setting is "auto") */
  hyphenate?: boolean;
}

/** Fill {page}, {pages} and {title} (running headers/footers). Measurement uses "00". */
export function fillTokens(s: string, ctx: RenderCtx, pageNo?: number): string {
  if (!s || !s.includes("{")) return s;
  return s.replace(/\{page\}/g, pageNo != null ? String(pageNo) : "00")
    .replace(/\{pages\}/g, ctx.pageCount ? String(ctx.pageCount) : "00")
    .replace(/\{title\}/g, ctx.title || "");
}

/** Label/title strip above a figure and the source line below it. */
function FigureFrame({ c, children }: { c: Record<string, any>; children: React.ReactNode }) {
  return (
    <>
      {(c.label || c.title) && (
        <div className="cd-fig-head">
          {c.label ? <span className="cd-fig-label">{c.label}</span> : null}
          {c.title ? <span className="cd-fig-title">{c.title}</span> : null}
        </div>
      )}
      {children}
      {c.source ? <div className="cd-fig-source">{c.source}</div> : null}
    </>
  );
}

/** The room a figure's label, source and caption take when each fits on one line. A figure with
 *  a fixed height gives the rest to its picture or chart; longer text wraps and takes more. */
function frameHeight(c: Record<string, any>, boxed: boolean): number {
  return (c.caption ? 28 : 0) + (c.label || c.title ? 26 : 0) + (c.source ? 22 : 0) + (boxed ? 24 : 0);
}

/** A figure with a fixed height is a column: the label, source and caption take the room their
 *  text needs (they wrap) and the picture or chart fills what is left. */
function figureStyle(fixedHeight?: number | null): React.CSSProperties {
  return fixedHeight
    ? { position: "relative", height: fixedHeight, boxSizing: "border-box", display: "flex", flexDirection: "column" }
    : { position: "relative" };
}
const FILL: React.CSSProperties = { flex: "1 1 0", minHeight: 0 };

/** The chart of a fixed-height figure: it is drawn at the height the label, source and caption
 *  leave it, so a caption that wraps makes the chart shorter and nothing is cut off. */
function FillChart({ spec, width, estimate }: { spec: any; width: number; estimate: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [h, setH] = useState(estimate);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => {
      const now = Math.max(24, Math.floor(el.clientHeight));
      setH((prev) => (Math.abs(prev - now) < 1 ? prev : now));
    };
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [estimate, width]);
  return <div ref={ref} style={{ ...FILL, overflow: "hidden" }}><Chart spec={spec} width={width} height={h} /></div>;
}

/** Step a table that is wider than its column through the fit levels in styles.css until it
 *  fits: 1 breaks long words at syllables, 2 also sets it smaller, 3 breaks a word anywhere. */
function fitTable(table: HTMLTableElement): void {
  const box = table.parentElement;
  if (!box) return;
  const cs = getComputedStyle(box);
  const room = box.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
  table.removeAttribute("data-fit");
  for (let level = 1; level <= 3 && table.offsetWidth > room + 0.5; level++) table.dataset.fit = String(level);
}

/** Fit every table in a block, and again when its width or the fonts change. It runs the same
 *  way in the measuring copy, the editor and the export, so they agree. */
function useFitTables(ref: React.RefObject<HTMLDivElement | null>, deps: unknown[]): void {
  useLayoutEffect(() => {
    const root = ref.current;
    const tables = root ? Array.from(root.querySelectorAll<HTMLTableElement>("table")) : [];
    if (!root || !tables.length) return;
    let width = root.clientWidth;
    const fit = () => tables.forEach(fitTable);
    fit();
    const fonts = typeof document !== "undefined" ? document.fonts : undefined;
    fonts?.addEventListener?.("loadingdone", fit);
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      if (root.clientWidth === width) return;   // only a change of width can change the fit
      width = root.clientWidth;
      fit();
    });
    ro?.observe(root);
    return () => {
      fonts?.removeEventListener?.("loadingdone", fit);
      ro?.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

const ALIGN = new Set(["left", "center", "right"]);
/** A table block's column alignment: content.align, one of left/center/right per column. */
function cellAlign(c: Record<string, any>, col: number): "left" | "center" | "right" | undefined {
  const a = Array.isArray(c.align) ? c.align[col] : undefined;
  return ALIGN.has(a) ? a : undefined;
}

export function imageSrc(src: string): string {
  if (!src) return "";
  if (/^upl_[a-z0-9]+$/i.test(src)) return withBase(`/api/uploads/${src}/file`);
  return src;
}

/** Read-only rendering of one block at a given width. Used by the measurer, the
 *  editor canvas, the preview and the export, so they always agree. */
export const BlockView = memo(function BlockView(props: {
  block: Block; width: number; ctx: RenderCtx; fixedHeight?: number | null; pageNo?: number;
}) {
  const { block, width, ctx } = props;
  const fallback = (
    <div className={`cd-block cd-type-${block.type}`} data-block={block.id} style={{ width }}>
      {ctx.editing ? <div className="cd-muted">This {block.type} block can't be shown. Edit or step back its version in the Block panel.</div> : <div />}
    </div>
  );
  return <Boundary key={block.head} fallback={fallback}><BlockBody {...props} /></Boundary>;
});

function BlockBody({ block, width, ctx, fixedHeight, pageNo }: {
  block: Block; width: number; ctx: RenderCtx; fixedHeight?: number | null; pageNo?: number;
}) {
  const c = block.content || {};
  const variant = block.layout?.variant || "";
  const boxed = variant === "figure-box";
  const html = useMemo(
    () => (block.type === "text" || block.type === "callout"
      ? mdToHtml(fillTokens(c.markdown || "", ctx, pageNo), { lang: ctx.lang, hyphenate: ctx.hyphenate }) : ""),
    [block.type, c.markdown, ctx, pageNo],
  );
  const rootRef = useRef<HTMLDivElement>(null);
  useFitTables(rootRef, [html, block.type === "table" ? c.rows : null, width, variant, ctx.lang]);
  const empty = (s: string) => ctx.editing && !s.trim();
  // Editor-only hint text in a box that flows: print renders the same text invisibly, so the
  // editor and the export measure the same height and paginate alike (gh #13).
  const hint = (text: string) => (ctx.editing ? text : <span className="cd-ghost" aria-hidden="true">{text}</span>);

  let inner: React.JSX.Element;
  switch (block.type) {
    case "text":
      inner = !(c.markdown || "").trim() ? (
        <div className={`cd-md${ctx.editing ? " cd-placeholder" : ""}`}><p>{hint("Type something…")}</p></div>
      ) : (
        <div className="cd-md" dangerouslySetInnerHTML={{ __html: html }} />
      );
      break;
    case "heading": {
      const L = Math.max(1, Math.min(3, Number(c.level) || 1));
      const Tag = (`h${L}`) as "h1" | "h2" | "h3";
      inner = <Tag className={`cd-h cd-h${L}${empty(c.text || "") ? " cd-placeholder" : ""}`}>{fillTokens(c.text || "", ctx, pageNo) || hint("Heading")}</Tag>;
      break;
    }
    case "callout":
      inner = (
        <div className={`cd-callout cd-tone-${c.tone || "info"}`}>
          {!(c.markdown || "").trim()
            ? <div className={`cd-md${ctx.editing ? " cd-placeholder" : ""}`}><p>{hint("Callout…")}</p></div>
            : <div className="cd-md" dangerouslySetInnerHTML={{ __html: html }} />}
        </div>
      );
      break;
    case "video": {
      const src = imageSrc(c.src || "");
      const poster = imageSrc(c.poster || "");
      const fill: React.CSSProperties = fixedHeight ? { width: "100%", ...FILL } : { width: "100%", height: "auto" };
      // Print/export shows the poster frame (headless Chromium can't decode H.264);
      // the editor and the HTML export play the video.
      const still = !ctx.editing && ctx.print;
      inner = (
        <figure className="cd-figure" style={figureStyle(fixedHeight)}>
          {!src ? (
            <div className="cd-image-empty" style={fixedHeight ? FILL : { height: 180 }}>{ctx.editing ? "Video: upload a file (MP4, WebM or GIF) in the Block panel" : ""}</div>
          ) : still && poster ? (
            <img src={poster} alt={c.caption || "video"} style={{ ...fill, objectFit: "contain" }} />
          ) : /\.gif$/i.test(c.src) || c.mime === "image/gif" ? (
            <img src={src} alt={c.caption || ""} style={{ ...fill, objectFit: "contain" }} />
          ) : (
            <video src={src} poster={poster || undefined} controls={!still} muted={c.muted !== false} loop={!!c.loop}
              playsInline preload="metadata" style={{ ...fill, background: "#000" }} />
          )}
          {c.caption ? <figcaption>{c.caption}</figcaption> : null}
        </figure>
      );
      break;
    }
    case "image": {
      const src = imageSrc(c.src || "") || (c.sketch ? imageSrc(c.sketch) : "");
      const isSketch = !c.src && !!c.sketch;
      inner = (
        <figure className="cd-figure" style={figureStyle(fixedHeight)}>
          <FigureFrame c={{ ...c, caption: "" }}>
          {isSketch && ctx.editing && <span className="cd-sketch-tag">Sketch: ask {ctx.agent || "the agent"} to turn it into an image</span>}
          {src ? (
            <img src={src} alt={c.alt || ""} style={fixedHeight
              ? { width: "100%", ...FILL, objectFit: c.fit || "contain" }
              : { width: "100%", height: "auto", maxHeight: 640, objectFit: "contain" }} />
          ) : (
            <div className="cd-image-empty" style={fixedHeight ? FILL : { height: 160 }}>
              {ctx.editing ? "Image: upload, paste a URL, or draw a sketch in the Block panel" : ""}
            </div>
          )}
          </FigureFrame>
          {c.caption ? <figcaption>{c.caption}</figcaption> : null}
        </figure>
      );
      break;
    }
    case "table": {
      const rows: string[][] = Array.isArray(c.rows) ? c.rows : [];
      const header = c.header !== false && rows.length > 0;
      inner = (
        <div className="cd-table-wrap">
          <table className="cd-table">
            {header && (
              <thead><tr>{rows[0].map((cell, i) => (
                <th key={i} align={cellAlign(c, i)}>{hyphenateText(String(cell ?? ""), ctx.lang, true)}</th>))}</tr></thead>
            )}
            <tbody>
              {(header ? rows.slice(1) : rows).map((r, i) => (
                <tr key={i}>{r.map((cell, j) => (
                  <td key={j} align={cellAlign(c, j)}>{hyphenateText(String(cell ?? ""), ctx.lang, true)}</td>))}</tr>
              ))}
            </tbody>
          </table>
          {c.caption ? <div className="cd-caption">{c.caption}</div> : null}
        </div>
      );
      break;
    }
    case "chart": {
      const cw = boxed ? width - 24 : width;
      inner = (
        <figure className="cd-figure" style={figureStyle(fixedHeight)}>
          <FigureFrame c={{ ...c, caption: "" }}>
            {fixedHeight
              ? <FillChart spec={c.spec} width={cw} estimate={Math.max(24, fixedHeight - frameHeight(c, boxed))} />
              : <Chart spec={c.spec} width={cw} height={Number(c.spec?.height) || DEFAULT_CHART_HEIGHT} />}
          </FigureFrame>
          {c.caption ? <figcaption>{c.caption}</figcaption> : null}
        </figure>
      );
      break;
    }
    case "divider":
      inner = <hr className="cd-divider" />;
      break;
    case "references":
      inner = (
        <div className="cd-refs">
          {c.title !== "" ? <div className="cd-h cd-h2">{c.title || "References"}</div> : null}
          {ctx.references.length ? (
            c.style === "hanging" || variant === "hanging" ? (
              <ul className="cd-refs-hanging">{ctx.references.map((r) => (
                <li key={r.n}>
                  {r.title}{r.note ? <> {r.note}</> : null}{r.url ? <> <span className="cd-ref-url">{r.url}</span></> : null}
                </li>
              ))}</ul>
            ) : (
              <ol>{ctx.references.map((r) => (
                <li key={r.n} value={r.n}>
                  {r.title}{r.url ? <> <a href={r.url}>{r.url}</a></> : null}{r.note ? <span className="cd-muted"> ({r.note})</span> : null}
                </li>
              ))}</ol>
            )
          ) : (
            <p className="cd-muted">{hint("No sources cited yet. Blocks' references are listed here automatically.")}</p>
          )}
        </div>
      );
      break;
    case "page-break":
      inner = ctx.editing ? <div className="cd-pagebreak">Page break</div> : <div />;
      break;
    case "spacer":
      // A reserved area: blank in print, hatched in the editor so it can be found and moved.
      inner = ctx.editing
        ? <div className="cd-spacer" style={{ height: fixedHeight || 48 }}><span>{c.label || "Reserved area"}</span></div>
        : <div style={{ height: fixedHeight || 48 }} />;
      break;
    default:
      inner = <div className="cd-muted">Unsupported block type: {block.type}</div>;
  }
  return (
    <div className={`cd-block cd-type-${block.type}${variant ? ` cd-v-${variant}` : ""}${block.layout?.dropcap ? " cd-dropcap" : ""}`}
      data-block={block.id} style={{ width }} ref={rootRef}>
      {inner}
    </div>
  );
}

/** All references in reading order, de-duplicated by url (or title). */
export function compileReferences(blocks: Block[]): (Ref & { n: number })[] {
  const seen = new Map<string, number>();
  const out: (Ref & { n: number })[] = [];
  for (const b of blocks) {
    for (const r of b.refs || []) {
      const key = (r.url || r.title).toLowerCase();
      if (seen.has(key)) continue;
      seen.set(key, out.length + 1);
      out.push({ ...r, n: out.length + 1 });
    }
  }
  return out;
}
