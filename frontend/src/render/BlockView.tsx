import type React from "react";
import { memo, useMemo } from "react";
import type { Block, Ref } from "../types";
import { Chart, DEFAULT_CHART_HEIGHT } from "./Chart";
import { mdToHtml } from "./markdown";

export interface RenderCtx {
  /** de-duplicated references in reading order, for `references` blocks */
  references: (Ref & { n: number })[];
  /** editor mode shows placeholders for empty blocks and visible page breaks */
  editing: boolean;
  /** read-only print route (export / view_pages) */
  print?: boolean;
}

function imageSrc(src: string): string {
  if (!src) return "";
  if (/^upl_[a-z0-9]+$/i.test(src)) return `/api/uploads/${src}/file`;
  return src;
}

/** Read-only rendering of one block at a given width. Used by the measurer, the
 *  editor canvas, the preview and the export, so they always agree. */
export const BlockView = memo(function BlockView({ block, width, ctx, fixedHeight }: {
  block: Block; width: number; ctx: RenderCtx; fixedHeight?: number | null;
}) {
  const c = block.content || {};
  const html = useMemo(
    () => (block.type === "text" || block.type === "callout" ? mdToHtml(c.markdown || "") : ""),
    [block.type, c.markdown],
  );
  const empty = (s: string) => ctx.editing && !s.trim();

  let inner: React.JSX.Element;
  switch (block.type) {
    case "text":
      inner = empty(c.markdown || "") ? (
        <div className="cd-md cd-placeholder"><p>Type something…</p></div>
      ) : (
        <div className="cd-md" dangerouslySetInnerHTML={{ __html: html }} />
      );
      break;
    case "heading": {
      const L = Math.max(1, Math.min(3, Number(c.level) || 1));
      const Tag = (`h${L}`) as "h1" | "h2" | "h3";
      inner = <Tag className={`cd-h cd-h${L}${empty(c.text || "") ? " cd-placeholder" : ""}`}>{c.text || (ctx.editing ? "Heading" : "")}</Tag>;
      break;
    }
    case "callout":
      inner = (
        <div className={`cd-callout cd-tone-${c.tone || "info"}`}>
          {empty(c.markdown || "") ? <div className="cd-md cd-placeholder"><p>Callout…</p></div>
            : <div className="cd-md" dangerouslySetInnerHTML={{ __html: html }} />}
        </div>
      );
      break;
    case "video": {
      const src = imageSrc(c.src || "");
      const poster = imageSrc(c.poster || "");
      const capH = c.caption ? 28 : 0;
      const h = fixedHeight ? fixedHeight - capH : undefined;
      // Print/export shows the poster frame (headless Chromium can't decode H.264);
      // the editor and the HTML export play the video.
      const still = !ctx.editing && ctx.print;
      inner = (
        <figure className="cd-figure">
          {!src ? (
            <div className="cd-image-empty" style={{ height: h ?? 180 }}>{ctx.editing ? "Video: upload a file (MP4, WebM or GIF) in the Block panel" : ""}</div>
          ) : still && poster ? (
            <img src={poster} alt={c.caption || "video"} style={{ width: "100%", height: h ?? "auto", objectFit: "contain" }} />
          ) : /\.gif$/i.test(c.src) || c.mime === "image/gif" ? (
            <img src={src} alt={c.caption || ""} style={{ width: "100%", height: h ?? "auto", objectFit: "contain" }} />
          ) : (
            <video src={src} poster={poster || undefined} controls={!still} muted={c.muted !== false} loop={!!c.loop}
              playsInline preload="metadata" style={{ width: "100%", height: h ?? "auto", background: "#000" }} />
          )}
          {c.caption ? <figcaption>{c.caption}</figcaption> : null}
        </figure>
      );
      break;
    }
    case "image": {
      const src = imageSrc(c.src || "") || (c.sketch ? imageSrc(c.sketch) : "");
      const isSketch = !c.src && !!c.sketch;
      const capH = c.caption ? 28 : 0;
      inner = (
        <figure className="cd-figure" style={{ position: "relative" }}>
          {isSketch && ctx.editing && <span className="cd-sketch-tag">Sketch: ask the agent to turn it into an image</span>}
          {src ? (
            <img src={src} alt={c.alt || ""} style={fixedHeight
              ? { width: "100%", height: fixedHeight - capH, objectFit: c.fit || "contain" }
              : { width: "100%", height: "auto", maxHeight: 640, objectFit: "contain" }} />
          ) : (
            <div className="cd-image-empty" style={{ height: fixedHeight ? fixedHeight - capH : 160 }}>
              {ctx.editing ? "Image: upload, paste a URL, or draw a sketch in the Block panel" : ""}
            </div>
          )}
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
              <thead><tr>{rows[0].map((cell, i) => <th key={i}>{String(cell ?? "")}</th>)}</tr></thead>
            )}
            <tbody>
              {(header ? rows.slice(1) : rows).map((r, i) => (
                <tr key={i}>{r.map((cell, j) => <td key={j}>{String(cell ?? "")}</td>)}</tr>
              ))}
            </tbody>
          </table>
          {c.caption ? <div className="cd-caption">{c.caption}</div> : null}
        </div>
      );
      break;
    }
    case "chart": {
      const capH = c.caption ? 28 : 0;
      const h = (fixedHeight ? fixedHeight - capH : Number(c.spec?.height) || DEFAULT_CHART_HEIGHT);
      inner = (
        <figure className="cd-figure">
          <Chart spec={c.spec} width={width} height={h} />
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
          <div className="cd-h cd-h2">{c.title || "References"}</div>
          {ctx.references.length ? (
            <ol>{ctx.references.map((r) => (
              <li key={r.n} value={r.n}>
                {r.title}{r.url ? <> <a href={r.url}>{r.url}</a></> : null}{r.note ? <span className="cd-muted"> ({r.note})</span> : null}
              </li>
            ))}</ol>
          ) : (
            <p className="cd-muted">{ctx.editing ? "No sources cited yet. Blocks' references are listed here automatically." : ""}</p>
          )}
        </div>
      );
      break;
    case "page-break":
      inner = ctx.editing ? <div className="cd-pagebreak">Page break</div> : <div />;
      break;
    default:
      inner = <div className="cd-muted">Unsupported block type: {block.type}</div>;
  }
  return (
    <div className={`cd-block cd-type-${block.type}`} data-block={block.id} style={{ width }}>
      {inner}
    </div>
  );
});

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
