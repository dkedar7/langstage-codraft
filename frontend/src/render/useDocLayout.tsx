import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { blockWidth, paginate, type LayoutResult, type MeasuredBlock, type Split } from "../layout/paginate";
import { geometry } from "../layout/units";
import type { Block, Settings, Theme } from "../types";
import { BlockView, compileReferences, type RenderCtx } from "./BlockView";
import { themeVars } from "./theme";

export interface Measured {
  height: number;
  natural: number;
  splits: Split[];
}

const SPLITTABLE_UNITS = new Set(["P", "LI", "BLOCKQUOTE", "PRE", "H1", "H2", "H3", "H4", "H5", "H6", "TABLE", "HR", "DIV"]);

/** Break points inside a rendered text block: between paragraphs/list items, and
 *  between lines of a paragraph (keeping at least 2 lines on each side). */
function textSplits(root: HTMLElement): Split[] {
  const md = root.querySelector(".cd-md");
  if (!md) return [];
  const top = root.getBoundingClientRect().top;
  const units: HTMLElement[] = [];
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
    if (i < units.length - 1) {
      const nextTop = units[i + 1].getBoundingClientRect().top - top;
      // Keep a heading with the paragraph that follows it.
      if (/^H\d$/.test(units[i].tagName)) continue;
      splits.push({ end: uBottom, next: Math.max(uBottom, nextTop) });
    }
  }
  return splits.sort((a, b) => a.end - b.end);
}

export function toMeasuredBlocks(blocks: Block[], m: Record<string, Measured>): MeasuredBlock[] {
  return blocks.map((b) => {
    const meas = m[b.id] || { height: 24, natural: 24, splits: [] };
    const fixed = b.layout.pin?.height || b.layout.height;
    return {
      id: b.id,
      kind: b.type === "page-break" ? "break" : b.type === "text" && !fixed ? "text" : "atomic",
      role: b.layout.role || "flow",
      full: b.layout.span === "full",
      height: fixed || meas.height,
      splits: meas.splits,
      pin: b.layout.pin && b.layout.role === "flow"
        ? { ...b.layout.pin, height: b.layout.pin.height || meas.height } : null,
      repeat: b.layout.repeat,
    };
  });
}

/**
 * Measure every block off-screen at the width it will occupy, then paginate.
 * Re-measures when the blocks, settings, theme or fonts change, or when anything
 * inside the measuring box resizes (images loading, charts rendering).
 */
export function useDocLayout(blocks: Block[], settings: Settings, theme: Theme | undefined, editing: boolean, print = false) {
  const geo = useMemo(() => geometry(settings), [settings]);
  const ctx: RenderCtx = useMemo(() => ({ references: compileReferences(blocks), editing, print }), [blocks, editing, print]);
  const boxRef = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState<Record<string, Measured>>({});
  const [tick, setTick] = useState(0);
  const [ready, setReady] = useState(false);

  const widths = useMemo(() => {
    const w: Record<string, number> = {};
    for (const b of blocks) {
      w[b.id] = blockWidth({ full: b.layout.span === "full", role: b.layout.role || "flow",
        pin: b.layout.pin && b.layout.role === "flow" ? { ...b.layout.pin, height: 0 } : null }, geo);
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
      const natural = root.getBoundingClientRect().height;
      const b = blocks.find((x) => x.id === id);
      const splits = b && b.type === "text" ? textSplits(root) : [];
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
    () => paginate(toMeasuredBlocks(blocks, measured), geo),
    [blocks, measured, geo],
  );

  const measureNode: ReactNode = (
    <div ref={boxRef} className="cd-doc cd-measure" aria-hidden style={themeVars(theme)}>
      {blocks.map((b) => (
        <div key={b.id} className="cd-measure-item" data-id={b.id} style={{ width: widths[b.id] }}>
          <BlockView block={b} width={widths[b.id]} ctx={ctx}
            fixedHeight={b.layout.pin?.height || b.layout.height} />
        </div>
      ))}
    </div>
  );

  const allMeasured = blocks.every((b) => measured[b.id]);
  return { layout, geo, ctx, measured, measureNode, ready: ready && allMeasured, widths };
}
