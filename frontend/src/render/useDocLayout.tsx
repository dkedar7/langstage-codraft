import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { blockWidth, paginate, type LayoutResult, type MeasuredBlock, type Split } from "../layout/paginate";
import { geometry } from "../layout/units";
import type { Block, Settings, Theme } from "../types";
import { BlockView, type RenderCtx } from "./BlockView";
import { citeNumbers, compileReferences } from "./refs";
import { useHyphenation } from "./hyphenate";
import { themeVars } from "./theme";

/** A BCP-47 code for hyphenation, from the brief's language. */
export function langCode(s: Settings): string {
  const l = (s.brief?.language || "en").toLowerCase();
  const map: Record<string, string> = { english: "en", spanish: "es", french: "fr", german: "de", portuguese: "pt",
    italian: "it", dutch: "nl", hindi: "hi", marathi: "mr", japanese: "ja", chinese: "zh" };
  return map[l] || (l.length <= 3 ? l : "en");
}

export interface Measured {
  height: number;
  natural: number;
  splits: Split[];
  /** a table's header row: the top of the block that every continued piece repeats */
  head?: number;
}

/** Rows a split keeps together on each side, so no piece is a lone header or a single row. */
const MIN_ROWS = 2;

/** The part of a row's bottom rule that lies below the row: with collapsed borders the rule
 *  straddles the line between two rows, so a cut at the line would slice it in half. */
function ruleBelow(row: HTMLTableRowElement | null | undefined): number {
  const cell = row?.cells[0];
  return cell ? Math.ceil((parseFloat(getComputedStyle(cell).borderBottomWidth) || 0) / 2) : 0;
}

/** Break points between the body rows of a table (y relative to `top`). A piece ends just
 *  below the rule under its last row, and the next one starts there. */
function rowSplits(table: HTMLTableElement, top: number): Split[] {
  const rows = Array.from(table.tBodies).flatMap((tb) => Array.from(tb.rows));
  const out: Split[] = [];
  for (let k = MIN_ROWS; k <= rows.length - MIN_ROWS; k++) {
    const y = rows[k].getBoundingClientRect().top - top + ruleBelow(rows[k - 1]);
    out.push({ end: y, next: y });
  }
  return out;
}

/** A table block: it splits between rows, and each continued piece repeats the header row. */
function tableSplits(root: HTMLElement): { splits: Split[]; head: number } {
  const table = root.querySelector<HTMLTableElement>("table.cd-table");
  if (!table) return { splits: [], head: 0 };
  const top = root.getBoundingClientRect().top;
  const first = table.tBodies[0]?.rows[0];
  const head = table.tHead && first
    ? Math.max(0, first.getBoundingClientRect().top - top + ruleBelow(table.tHead.rows[table.tHead.rows.length - 1])) : 0;
  return { splits: rowSplits(table, top), head };
}

const SPLITTABLE_UNITS = new Set(["P", "LI", "BLOCKQUOTE", "PRE", "H1", "H2", "H3", "H4", "H5", "H6", "TABLE", "HR", "DIV"]);

/** Break points inside a rendered text block: between paragraphs/list items, and
 *  between lines of a paragraph (keeping at least 2 lines on each side). */
function textSplits(root: HTMLElement): Split[] {
  const top = root.getBoundingClientRect().top;
  const units: HTMLElement[] = [];
  const refs = root.querySelector(".cd-refs");
  if (refs) {
    // References split between entries (the list title stays with the first one).
    units.push(...(Array.from(refs.querySelectorAll(":scope > ol > li, :scope > ul > li")) as HTMLElement[]));
    return units.slice(0, -1).map((li, i) => {
      const b = li.getBoundingClientRect().bottom - top;
      const n = units[i + 1].getBoundingClientRect().top - top;
      return { end: b, next: Math.max(b, n) };
    }).filter((s) => s.end > 0);
  }
  const md = root.querySelector(".cd-md");
  if (!md) return [];
  for (const el of Array.from(md.children) as HTMLElement[]) {
    if ((el.tagName === "UL" || el.tagName === "OL") && el.children.length) {
      units.push(...(Array.from(el.children) as HTMLElement[]));
    } else if (SPLITTABLE_UNITS.has(el.tagName)) {
      units.push(el);
    }
  }
  const splits: Split[] = [];
  for (let i = 0; i < units.length; i++) {
    const r = units[i].getBoundingClientRect();
    const uTop = r.top - top;
    const uBottom = r.bottom - top;
    if (units[i].tagName === "P") {
      const lh = parseFloat(getComputedStyle(units[i]).lineHeight) || 0;
      const lines = lh > 0 ? Math.round((uBottom - uTop) / lh) : 1;
      for (let k = 2; k <= lines - 2; k++) {
        const y = uTop + k * lh;
        splits.push({ end: y, next: y });
      }
    }
    // A Markdown table inside the text splits between its rows too (its header is not repeated).
    if (units[i].tagName === "TABLE") splits.push(...rowSplits(units[i] as HTMLTableElement, top));
    if (i < units.length - 1) {
      const nextTop = units[i + 1].getBoundingClientRect().top - top;
      // Keep a heading with the paragraph that follows it.
      if (/^H\d$/.test(units[i].tagName)) continue;
      splits.push({ end: uBottom, next: Math.max(uBottom, nextTop) });
    }
  }
  return splits.sort((a, b) => a.end - b.end);
}

/** The fixed height a pinned block reserves, if any. */
export function pinHeight(b: Block): number | null {
  const p = b.layout.pin;
  if (!p) return null;
  return (p.anchor === "page" ? p.h : p.height) || null;
}

/** Block types that split across pages (at the points measured in the DOM). */
const SPLITS = new Set(["text", "references", "table"]);
/** Figure-like blocks: with back-fill on, one that does not fit lets the text after it fill the gap. */
const FLOATS = new Set(["image", "video", "chart", "row", "stat"]);
/** Blocks whose content can be taller than a fixed height (and is then cut off). */
export const CLIPS = new Set(["text", "callout", "row", "stat", "heading", "table", "references"]);

export function toMeasuredBlocks(blocks: Block[], m: Record<string, Measured>, opts: { backfill?: boolean } = {}): MeasuredBlock[] {
  return blocks.map((b) => {
    const meas = m[b.id] || { height: 24, natural: 24, splits: [] };
    const fixed = pinHeight(b) || b.layout.height;
    const flows = (b.layout.role || "flow") === "flow" && !b.layout.pin;
    return {
      id: b.id,
      kind: b.type === "page-break" ? "break" : SPLITS.has(b.type) && !fixed && !b.layout.pin ? "text" : "atomic",
      role: b.layout.role || "flow",
      full: b.layout.span === "full",
      height: fixed || meas.height,
      splits: meas.splits,
      head: meas.head || 0,
      float: !!opts.backfill && flows && FLOATS.has(b.type) && b.layout.anchor !== "bottom",
      anchor: flows && b.layout.anchor === "bottom" ? "bottom" : null,
      natural: fixed && CLIPS.has(b.type) ? meas.natural : undefined,
      clips: CLIPS.has(b.type),
      pin: b.layout.pin
        ? b.layout.pin.anchor === "page"
          ? { anchor: "page" as const, page: b.layout.pin.page, top: b.layout.pin.y ?? 0, x: b.layout.pin.x ?? 0,
              w: b.layout.pin.w ?? 300, column: 0, span: 1, height: b.layout.pin.h || meas.height }
          : { ...b.layout.pin, height: b.layout.pin.height || meas.height }
        : null,
      repeat: b.layout.repeat,
      keepWithNext: b.type === "heading" && !b.layout.pin,
    };
  });
}

/**
 * Measure every block off-screen at the width it will occupy, then paginate.
 * Re-measures when the blocks, settings, theme or fonts change, or when anything
 * inside the measuring box resizes (images loading, charts rendering).
 */
export function useDocLayout(blocks: Block[], settings: Settings, theme: Theme | undefined, editing: boolean, print = false) {
  const geo = useMemo(() => geometry(settings, theme), [settings, theme]);
  const lang = langCode(settings);
  const canHyphenate = useHyphenation(lang);   // re-renders when a language's patterns arrive
  const hyphenate = canHyphenate && theme?.hyphens === "auto";
  // The theme's colours, for blocks with a fill. Keyed on the values so a theme object that is
  // rebuilt on every render (the print route) doesn't re-render every block.
  const colorKey = JSON.stringify([theme?.color_accent, theme?.color_text, theme?.color_muted, theme?.color_heading,
    theme?.color_callout, theme?.color_figure, theme?.color_rule]);
  const colors = useMemo(() => {
    const [color_accent, color_text, color_muted, color_heading, color_callout, color_figure, color_rule] = JSON.parse(colorKey);
    return { color_accent, color_text, color_muted, color_heading, color_callout, color_figure, color_rule } as Partial<Theme>;
  }, [colorKey]);
  const ctx: RenderCtx = useMemo(() => {
    const references = compileReferences(blocks);
    return { references, cites: citeNumbers(blocks, references), editing, print, pageCount: 0,
      lang: canHyphenate ? lang : null, hyphenate, colors, colGap: geo.colGap };
  },
    [blocks, editing, print, lang, canHyphenate, hyphenate, colors, geo.colGap]);
  const boxRef = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState<Record<string, Measured>>({});
  const [tick, setTick] = useState(0);
  const [ready, setReady] = useState(false);

  const widths = useMemo(() => {
    const w: Record<string, number> = {};
    for (const b of blocks) {
      const pin = b.layout.pin;
      w[b.id] = blockWidth({ full: b.layout.span === "full", role: b.layout.role || "flow",
        pin: pin ? { ...pin, height: 0, top: 0, x: pin.x ?? 0, w: pin.anchor === "page" ? pin.w ?? 300 : undefined } : null }, geo);
    }
    return w;
  }, [blocks, geo]);

  const measure = useCallback(() => {
    const box = boxRef.current;
    if (!box) return;
    const out: Record<string, Measured> = {};
    for (const el of Array.from(box.querySelectorAll<HTMLElement>(":scope > .cd-measure-item"))) {
      const id = el.dataset.id!;
      const root = el.firstElementChild as HTMLElement | null;
      if (!root) continue;
      const b = blocks.find((x) => x.id === id);
      const fixed = b ? pinHeight(b) || b.layout.height : null;
      const box = root.getBoundingClientRect().height;
      // A block with a fixed height keeps it; its content's own height says whether it is cut off.
      const natural = fixed ? Math.max(box, root.scrollHeight) : box;
      if (b?.type === "table") {
        const t = tableSplits(root);
        out[id] = { height: Math.ceil(natural), natural, splits: t.splits, head: t.head };
        continue;
      }
      const splits = b && (b.type === "text" || b.type === "references") ? textSplits(root) : [];
      out[id] = { height: Math.ceil(natural), natural, splits };
    }
    setMeasured((prev) => (JSON.stringify(prev) === JSON.stringify(out) ? prev : out));
  }, [blocks]);

  useLayoutEffect(() => {
    measure();
  }, [measure, widths, theme, tick]);

  useEffect(() => {
    let alive = true;
    document.fonts?.ready.then(() => {
      if (alive) {
        setTick((t) => t + 1);
        setReady(true);
      }
    });
    const box = boxRef.current;
    if (!box || typeof ResizeObserver === "undefined") return () => { alive = false; };
    let raf = 0;
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => measure());
    });
    for (const el of Array.from(box.children)) ro.observe(el.firstElementChild || el);
    return () => {
      alive = false;
      ro.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [measure, blocks]);

  const layout: LayoutResult = useMemo(
    () => paginate(toMeasuredBlocks(blocks, measured, { backfill: !!settings.backfill }), geo),
    [blocks, measured, geo, settings.backfill],
  );

  const measureNode: ReactNode = (
    <div ref={boxRef} className="cd-doc cd-measure" aria-hidden style={themeVars(theme)} lang={langCode(settings)}>
      {blocks.map((b) => (
        <div key={b.id} className="cd-measure-item" data-id={b.id} style={{ width: widths[b.id] }}>
          <BlockView block={b} width={widths[b.id]} ctx={ctx}
            fixedHeight={pinHeight(b) || b.layout.height} />
        </div>
      ))}
    </div>
  );

  const allMeasured = blocks.every((b) => measured[b.id]);
  return { layout, geo, ctx, measured, measureNode, ready: ready && allMeasured, widths };
}
