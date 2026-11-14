// Layout building blocks: a row of cells, number tiles (stat), and a fill behind a block
// (a band heading, a call-to-action callout, row cards, coloured tiles). Each is one atomic
// block the paginator measures like a table, so they flow with the document and need no pins.
// The Python side (store.py, themes.py) validates the same shapes.
import type { Block, Theme } from "../types";
import { MM } from "../layout/units";
import { hexLuminance } from "./theme";

export const ROW_CELLS: [number, number] = [2, 4];
export const STAT_ITEMS: [number, number] = [1, 6];

/** A row's cells as Markdown strings. Anything malformed reads as an empty cell, and there
 *  are always at least two, so a damaged block still renders as a row. */
export function rowCells(content: Record<string, any> | undefined): string[] {
  const raw = Array.isArray(content?.cells) ? content!.cells : [];
  const cells: string[] = raw.slice(0, ROW_CELLS[1]).map((c: any) =>
    typeof c === "string" ? c : c && typeof c.markdown === "string" ? c.markdown : "");
  while (cells.length < ROW_CELLS[0]) cells.push("");
  return cells;
}

/** CSS grid columns for a row: the relative widths if there is one valid number per cell,
 *  else equal cells. `minmax(0, …)` lets a cell shrink below its longest word, which then
 *  wraps the way it does in a column. */
export function rowColumns(widths: unknown, n: number): string {
  const ok = Array.isArray(widths) && widths.length === n
    && widths.every((w) => typeof w === "number" && Number.isFinite(w) && w > 0);
  return ok
    ? (widths as number[]).map((w) => `minmax(0, ${w}fr)`).join(" ")
    : `repeat(${n}, minmax(0, 1fr))`;
}

/** The gap between a row's cells in px: its own (mm), else the document's column gap. */
export function rowGap(content: Record<string, any> | undefined, colGap: number | undefined): number {
  const g = content?.gap;
  return typeof g === "number" && Number.isFinite(g) && g >= 0 ? g * MM : colGap ?? 24;
}

/** Cells narrower than this stack instead (a row put in one column of a three-column page). */
export const MIN_CELL_PX = 96;

export interface StatItem { value: string; label: string }

/** A stat block's tiles. Malformed entries are dropped; an empty block still shows one tile. */
export function statItems(content: Record<string, any> | undefined): StatItem[] {
  const raw = Array.isArray(content?.items) ? content!.items : [];
  const items = raw.slice(0, STAT_ITEMS[1])
    .filter((i: any) => i && typeof i === "object" && !Array.isArray(i))
    .map((i: any) => ({ value: i.value == null ? "" : String(i.value), label: i.label == null ? "" : String(i.label) }));
  return items.length ? items : [{ value: "", label: "" }];
}

/** How many tiles sit on one line: all of them while each keeps a usable width, else two lines. */
export function statColumns(n: number, width: number, gap: number): number {
  const tile = (cols: number) => (width - gap * (cols - 1)) / cols;
  if (n <= 1 || tile(n) >= 104) return Math.max(1, n);
  return Math.max(1, Math.ceil(n / 2));
}

// ---------------------------------------------------------------------------- fill

/** layout.fill: a hex colour, or the name of one of the theme's colours (themes.FILL_NAMES). */
export const FILL_NAMES: Record<string, keyof Theme> = {
  accent: "color_accent", text: "color_text", muted: "color_muted", heading: "color_heading",
  callout: "color_callout", figure: "color_figure", rule: "color_rule",
};
export const FILL_LABELS: Record<string, string> = {
  accent: "Accent", text: "Text colour", muted: "Muted", heading: "Heading colour",
  callout: "Callout tint", figure: "Figure tint", rule: "Rule colour",
};
/** The block types a fill applies to. */
export const FILL_TYPES = new Set(["heading", "callout", "row", "stat"]);
const HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;

export interface Fill { color: string; dark: boolean }

/** The colour a fill names in this theme, and whether the text on it must be light. */
export function resolveFill(fill: string | null | undefined, theme: Partial<Theme> | undefined): Fill | null {
  if (!fill) return null;
  let color: string | undefined = fill;
  if (fill in FILL_NAMES) {
    color = theme?.[FILL_NAMES[fill]] as string | undefined;
    if (!color && fill === "heading") color = theme?.color_text;
    if (!color && fill === "figure") color = theme?.color_callout;
  } else if (!HEX.test(fill)) {
    return null;
  }
  if (!color) return null;
  const lum = hexLuminance(color);
  return { color, dark: lum != null && lum < 0.4 };
}

/** The fill a block is drawn with: its own, or the accent for a band that names none. */
export function blockFill(block: Pick<Block, "type" | "layout">, theme: Partial<Theme> | undefined): Fill | null {
  if (!FILL_TYPES.has(block.type)) return null;
  const own = block.layout?.fill;
  if (own) return resolveFill(own, theme);
  if (block.layout?.variant === "band") return resolveFill("accent", theme);
  return null;
}
