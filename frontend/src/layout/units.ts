import type { Settings } from "../types";
import type { Geometry } from "./paginate";

/** CSS px per mm at 96 dpi. Mirrors langstage_codraft/pages.py. */
export const MM = 96 / 25.4;

export const PAGE_SIZES_MM: Record<string, [number, number]> = {
  letter: [215.9, 279.4],
  a4: [210, 297],
  legal: [215.9, 355.6],
  a5: [148, 210],
  slide: [338.67, 190.5],
};

export const PAGE_SIZE_LABELS: Record<string, string> = {
  letter: "US Letter",
  a4: "A4",
  legal: "US Legal",
  a5: "A5",
  slide: "Slide 16:9",
  custom: "Custom size",
};

export function pageMm(s: Settings): [number, number] {
  let [w, h] = s.page_size === "custom" && s.page_w_mm && s.page_h_mm
    ? [s.page_w_mm, s.page_h_mm]
    : PAGE_SIZES_MM[s.page_size] || PAGE_SIZES_MM.letter;
  if (s.orientation === "landscape") [w, h] = [h, w];
  return [w, h];
}

export function geometry(s: Settings): Geometry {
  const [w, h] = pageMm(s);
  const m = s.margins;
  return {
    mode: s.mode,
    pageW: w * MM,
    pageH: h * MM,
    margin: { top: m.top * MM, right: m.right * MM, bottom: m.bottom * MM, left: m.left * MM },
    columns: Math.max(1, Math.min(3, Number(s.columns) || 1)),
    colGap: s.column_gap * MM,
    blockGap: s.block_gap * MM,
    minPages: Math.max(1, s.min_pages || 1),
  };
}
