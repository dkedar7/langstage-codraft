import type { PageLayout } from "./paginate";

export interface NumberingOptions {
  /** number printed on the first numbered page */
  start: number;
  /** count cover and back pages (default false: they are skipped) */
  numberCoverPages: boolean;
  /** pages (by position, 1-based) that show no number and don't use one up */
  unnumbered: number[];
}

/**
 * Display page numbers: page position (1-based) -> printed number, or null for a page
 * without one. Shared by the editor and the export so they always agree.
 */
export function pageNumbers(pages: Pick<PageLayout, "kind" | "number">[], opt: NumberingOptions) {
  const skip = new Set(opt.unnumbered);
  const map: Record<number, number | null> = {};
  let next = opt.start;
  for (const p of pages) {
    const exempt = (!opt.numberCoverPages && p.kind !== "body") || skip.has(p.number);
    if (exempt) {
      map[p.number] = null;
    } else {
      map[p.number] = next;
      next += 1;
    }
  }
  const last = next - 1;
  return { map, count: Math.max(0, next - opt.start), last };
}

/** Does this block print the page number (so it hides on unnumbered pages)? */
export function usesPageToken(content: Record<string, any> | undefined): boolean {
  const s = `${content?.markdown ?? ""}${content?.text ?? ""}`;
  return s.includes("{page}");
}
