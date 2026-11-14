/**
 * The block paginator: a pure, deterministic layout of measured blocks onto pages.
 *
 * Design (see docs/brainstorms, R8–R15):
 *  - Blocks flow in order, column-major, through 1–3 columns per page.
 *  - A block is either single-column or full-width. A full-width block ends the
 *    current "band" of column content. That band is balanced across columns, as
 *    with a continuous section break, and the full-width block sits below it.
 *  - Pinned blocks are rectangle reservations on a page. Flowing blocks fill the
 *    space around them; this is never text wrapping.
 *  - Text blocks may split at the break points measured in the DOM (paragraph
 *    boundaries and line boundaries, with widow/orphan control). A table splits between
 *    its rows the same way, and each continued piece repeats the header row (`head`).
 *    Every other block is atomic: if it doesn't fit, it moves to the next column or page.
 *    An atomic block taller than a whole empty column is placed anyway and flagged as overflow.
 *  - A floating atomic block (`float`: a figure, chart, picture, row or tiles in a document
 *    with back-fill on) that doesn't fit lets the text after it fill the gap, and opens the
 *    next column or page itself. Only text up to the next heading moves ahead of it, one
 *    block floats at a time (so figures keep their order), and each floats once.
 *  - A block anchored to the bottom (`anchor: "bottom"`) sits at the foot of the page the
 *    flow has reached, and later blocks flow in the space above it.
 *  - A keep-with-next block (a heading) is never the last thing in a column: if the
 *    start of the block after it doesn't fit below it, it moves on with that block.
 *  - Cover and back blocks get their own pages, stacked in a single column (centred by
 *    default, or top/bottom). Those pages are exempt from columns and repeated blocks.
 *    A pinned cover/back block sits at its rectangle (art, a logo, a band) and the stack
 *    keeps clear of it; a pin taller than 60% of the page is a backdrop the stack covers.
 *  - With the cover turned off, the cover blocks open page 1 as a full-width title block.
 *
 * Nothing here touches the DOM, so the same function lays out the editor, the
 * preview and the export (parity by construction), and it is unit tested.
 */

export interface Split {
  /** y (block-relative) where a fragment may end */
  end: number;
  /** y where the next fragment starts (>= end; skips the inter-paragraph margin) */
  next: number;
}

export interface MeasuredBlock {
  id: string;
  kind: "text" | "atomic" | "break";
  role: "flow" | "cover" | "back";
  full: boolean;
  height: number;
  splits?: Split[];
  /** Column pin (top/column/span) or, with anchor "page", a page-relative rectangle (x/y/w) that may bleed past the margins. */
  pin?: { page: number; top: number; column: number; span: number; height: number; anchor?: "page"; x?: number; w?: number } | null;
  repeat?: "all" | "odd" | "even" | number[] | null;
  /** A heading: keep it in the same column as the start of the next block (gh #18). */
  keepWithNext?: boolean;
  /** A table's header row: this much of the block's top is drawn again above every continued piece. */
  head?: number;
  /** An atomic block that may let the text after it back-fill a gap it can't fit in. */
  float?: boolean;
  /** Sit at the bottom of the page the flow has reached. */
  anchor?: "bottom" | null;
  /** The content's own height when the block has a fixed one: a pinned block whose content
   *  is taller than its box is flagged as overflow. */
  natural?: number;
}

export interface Geometry {
  mode: "paged" | "pageless";
  pageW: number;
  pageH: number;
  margin: { top: number; right: number; bottom: number; left: number };
  columns: number;
  colGap: number;
  blockGap: number;
  minPages: number;
  /** cover page on/off and where its stack sits (default: on, centred) */
  cover?: { enabled: boolean; align: StackAlign };
  back?: { align: StackAlign };
}

export type StackAlign = "top" | "center" | "bottom";

export interface Placed {
  id: string;
  /** relative to the page's content box */
  x: number;
  y: number;
  w: number;
  h: number;
  /** block-relative y shown at the top of this item (for text fragments) */
  offset: number;
  fragment: boolean;
  continued: boolean;
  pinned: boolean;
  repeated: boolean;
  overflow: boolean;
  /** A continued table piece: this much of the block's top (its header row) is drawn again
   *  above the piece. Only set when it applies. */
  head?: number;
}

export interface PageLayout {
  kind: "cover" | "body" | "back";
  number: number;
  items: Placed[];
  height: number;
}

export interface LayoutResult {
  pages: PageLayout[];
  /** block id -> page numbers it appears on */
  blockPages: Record<string, number[]>;
  contentW: number;
  contentH: number;
  colW: number;
}

const EPS = 0.5;

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface Cursor {
  b: MeasuredBlock;
  offset: number;
  /** this block has already stepped aside once for the text after it */
  floated?: boolean;
}

/** The header a continued piece of this block repeats above itself. */
const headOf = (c: Cursor): number => (c.offset > 0 ? c.b.head || 0 : 0);

/** Text that may move ahead of a floating block: body text, never a heading. */
const backfills = (c: Cursor | undefined): boolean =>
  !!c && c.b.kind === "text" && !c.b.keepWithNext && !c.b.anchor && !c.b.full;

export function contentSize(geo: Geometry) {
  const contentW = geo.pageW - geo.margin.left - geo.margin.right;
  const contentH = geo.pageH - geo.margin.top - geo.margin.bottom;
  const n = Math.max(1, Math.min(3, geo.columns));
  const colW = (contentW - geo.colGap * (n - 1)) / n;
  return { contentW, contentH, colW, n };
}

/** Width a block is measured at. */
export function blockWidth(b: Pick<MeasuredBlock, "full" | "role" | "pin">, geo: Geometry): number {
  const { contentW, colW } = contentSize(geo);
  if (geo.mode === "pageless") return Math.min(contentW, 720);
  if (b.pin?.anchor === "page") return b.pin.w || contentW;
  if (b.role !== "flow") return contentW;
  if (b.pin) return b.pin.span * colW + (b.pin.span - 1) * geo.colGap;
  return b.full ? contentW : colW;
}

function repeatsOn(rep: MeasuredBlock["repeat"], page: number, pinPage: number): boolean {
  if (!rep) return page === pinPage;
  if (rep === "all") return true;
  if (rep === "odd") return page % 2 === 1;
  if (rep === "even") return page % 2 === 0;
  return rep.includes(page);
}

function pinRect(b: MeasuredBlock, geo: Geometry): Rect {
  const { colW } = contentSize(geo);
  const p = b.pin!;
  if (p.anchor === "page") {
    // page coordinates -> content-box coordinates (negative = in the margin / bleed)
    return { x: (p.x || 0) - geo.margin.left, y: p.top - geo.margin.top, w: p.w || 100, h: p.height || b.height };
  }
  return {
    x: p.column * (colW + geo.colGap),
    y: p.top,
    w: p.span * colW + (p.span - 1) * geo.colGap,
    h: p.height || b.height,
  };
}

/** Largest split end e with offset < e <= offset + room, honoring the fragment rules. */
function bestSplit(c: Cursor, room: number): Split | null {
  const splits = c.b.splits || [];
  let best: Split | null = null;
  const head = headOf(c);
  for (const s of splits) {
    if (s.end <= c.offset + EPS) continue;
    if (s.end - c.offset + head <= room + EPS) best = s;
    else break;
  }
  return best;
}

/**
 * Height needed from the top of cursors[qi] (a keep-with-next block) to the end of the
 * first piece of the block it introduces: any chained keep-with-next blocks, then the
 * first fragment of a text block or a whole atomic one. null when nothing follows in
 * the queue (no constraint).
 */
function leadHeight(cursors: Cursor[], qi: number, gap: number): number | null {
  let h = 0;
  let k = qi;
  while (k < cursors.length && cursors[k].b.keepWithNext) {
    h += (k > qi ? gap : 0) + cursors[k].b.height - cursors[k].offset;
    k++;
  }
  if (k >= cursors.length) return null;
  let d = cursors[k];
  // A figure that can step aside doesn't hold the heading back: the text after the figure
  // is what the heading has to stay with.
  if (d.b.float && d.b.kind === "atomic" && !d.floated && backfills(cursors[k + 1])) d = cursors[k + 1];
  const first = d.b.kind === "text" ? (d.b.splits || []).find((s) => s.end > d.offset + EPS) : undefined;
  return h + gap + (first ? first.end - d.offset : d.b.height - d.offset);
}

class PageBuilder {
  items: Placed[] = [];
  reserved: Rect[] = [];
  /** blocks anchored to the bottom of this page */
  bottoms = new Set<string>();
  constructor(
    public number: number,
    public bodyIndex: number,
    public geo: Geometry,
  ) {}
}

export function paginate(blocks: MeasuredBlock[], geo: Geometry): LayoutResult {
  const { contentW, contentH, colW, n } = contentSize(geo);
  const pages: PageLayout[] = [];
  const blockPages: Record<string, number[]> = {};
  const note = (id: string, page: number) => {
    (blockPages[id] ||= []).includes(page) || blockPages[id].push(page);
  };

  if (geo.mode === "pageless") {
    const w = Math.min(contentW, 720);
    let y = 0;
    const items: Placed[] = [];
    for (const b of blocks) {
      if (b.kind === "break") continue;
      if (items.length) y += geo.blockGap;
      items.push({ id: b.id, x: 0, y, w, h: b.height, offset: 0, fragment: false, continued: false,
        pinned: false, repeated: false, overflow: false });
      note(b.id, 1);
      y += b.height;
    }
    pages.push({ kind: "body", number: 1, items, height: y });
    return { pages, blockPages, contentW: w, contentH: y, colW: w };
  }

  let cover = blocks.filter((b) => b.role === "cover" && b.kind !== "break");
  const back = blocks.filter((b) => b.role === "back" && b.kind !== "break");
  let flow = blocks.filter((b) => b.role === "flow");
  if (geo.cover && !geo.cover.enabled && cover.length) {
    // No cover page: the cover blocks become a full-width title block opening page 1.
    const title = cover.map((b): MeasuredBlock => ({
      ...b, role: "flow", full: true, repeat: null,
      pin: b.pin ? (b.pin.anchor === "page" ? { ...b.pin, page: 1 } : { ...b.pin, page: 1, column: 0, span: n }) : null,
    }));
    flow = [...title, ...flow];
    cover = [];
  }
  const pinned = flow.filter((b) => b.pin);
  const flowing = flow.filter((b) => !b.pin);

  const stackPage = (kind: "cover" | "back", list: MeasuredBlock[], align: StackAlign) => {
    const number = pages.length + 1;
    const items: Placed[] = [];
    // Pinned blocks first (so the stack draws over them), and the band they leave free.
    let top = 0;
    let bottom = contentH;
    for (const b of list.filter((x) => x.pin)) {
      const p = b.pin!;
      const r = p.anchor === "page" ? pinRect(b, geo) : { x: 0, y: p.top, w: contentW, h: p.height || b.height };
      items.push({ id: b.id, x: r.x, y: r.y, w: r.w, h: r.h, offset: 0, fragment: false, continued: false,
        pinned: true, repeated: false, overflow: false });
      note(b.id, number);
      if (r.h >= contentH * 0.6) continue; // a backdrop: the stack sits on top of it
      if (r.y + r.h / 2 < contentH / 2) top = Math.max(top, r.y + r.h + geo.blockGap);
      else bottom = Math.min(bottom, r.y - geo.blockGap);
    }
    if (bottom - top < 40) {
      top = 0;
      bottom = contentH;
    }
    const stack = list.filter((x) => !x.pin);
    const total = stack.reduce((s, b) => s + b.height, 0) + geo.blockGap * Math.max(0, stack.length - 1);
    let y = align === "top" ? top : align === "bottom" ? Math.max(top, bottom - total)
      : top + Math.max(0, (bottom - top - total) / 2);
    for (const b of stack) {
      const overflow = y + b.height > contentH + EPS;
      items.push({ id: b.id, x: 0, y, w: contentW, h: Math.min(b.height, Math.max(0, contentH - y)),
        offset: 0, fragment: false, continued: false, pinned: false, repeated: false, overflow });
      note(b.id, number);
      y += b.height + geo.blockGap;
    }
    pages.push({ kind, number, items, height: contentH });
  };

  if (cover.length) stackPage("cover", cover, geo.cover?.align || "center");

  // ------------------------------------------------------------ body pages
  const firstBodyNumber = pages.length + 1;
  const body: PageBuilder[] = [];
  const newPage = (): PageBuilder => {
    const bodyIndex = body.length + 1;
    const pb = new PageBuilder(firstBodyNumber + body.length, bodyIndex, geo);
    for (const b of pinned) {
      if (repeatsOn(b.repeat ?? null, bodyIndex, b.pin!.page)) {
        const r = pinRect(b, geo);
        pb.reserved.push(r);
        pb.items.push({ id: b.id, x: r.x, y: r.y, w: r.w, h: r.h, offset: 0, fragment: false, continued: false,
          pinned: true, repeated: !!b.repeat && bodyIndex !== b.pin!.page,
          // A page-anchored pin may bleed into the margin: it overflows only past the page's edge.
          overflow: r.y + r.h > contentH + (b.pin!.anchor === "page" ? geo.margin.bottom : 0) + EPS
            || (b.natural != null && b.natural > r.h + 1) });
        note(b.id, pb.number);
      }
    }
    body.push(pb);
    return pb;
  };

  /** Reserved rects intersecting the horizontal span [x, x+w). */
  const blockersFor = (pb: PageBuilder, x: number, w: number) =>
    pb.reserved.filter((r) => r.x < x + w - EPS && r.x + r.w > x + EPS).sort((a, b) => a.y - b.y);

  /** Free vertical interval at or after y within [x, x+w) on the page, capped at limit. */
  const freeAt = (pb: PageBuilder, x: number, w: number, y: number, limit: number): { start: number; end: number } | null => {
    let cur = y;
    for (const r of blockersFor(pb, x, w)) {
      if (r.y + r.h <= cur + EPS) continue;
      if (r.y <= cur + EPS) {
        cur = r.y + r.h + geo.blockGap;
        continue;
      }
      return cur < limit - EPS ? { start: cur, end: Math.min(r.y - geo.blockGap, limit) } : null;
    }
    return cur < limit - EPS ? { start: cur, end: limit } : null;
  };

  let lastCol = 0;
  const place = (pb: PageBuilder, c: Cursor, x: number, y: number, w: number, h: number, end: number, overflow = false) => {
    const item: Placed = { id: c.b.id, x, y, w, h, offset: c.offset, fragment: c.offset > 0 || end < c.b.height - EPS,
      continued: c.offset > 0, pinned: false, repeated: false, overflow };
    if (headOf(c)) item.head = headOf(c);
    pb.items.push(item);
    lastCol = Math.max(0, Math.min(n - 1, Math.round(x / (colW + geo.colGap))));
    note(c.b.id, pb.number);
  };

  /**
   * Column-major fill of `queue` into this page's columns between startY and limit.
   * With dryRun, nothing is recorded; the return says whether everything fit.
   * Returns the cursor index/offset where it stopped and the lowest y used.
   */
  const fillBand = (pb: PageBuilder, queue: Cursor[], startY: number, limit: number, dryRun: boolean) => {
    let qi = 0;
    let bottom = startY;
    const cursors = queue.map((c) => ({ ...c }));
    // A floating block that didn't fit, waiting for the next interval while text back-fills.
    let held: Cursor | null = null;
    const release = () => {
      if (held) cursors.splice(qi, 0, held);
      held = null;
    };
    for (let col = 0; col < n && qi < cursors.length; col++) {
      const x = col * (colW + geo.colGap);
      let y = startY;
      let usedInInterval = false;
      while (qi < cursors.length || held) {
        // The held block comes back once nothing after it may move ahead of it.
        if (held && !backfills(cursors[qi])) release();
        const iv = freeAt(pb, x, colW, y, limit);
        if (!iv) break;
        if (iv.start > y + EPS) {
          y = iv.start;
          usedInInterval = false;
        }
        const c = cursors[qi];
        const gap = usedInInterval ? geo.blockGap : 0;
        const head = headOf(c);
        const remaining = c.b.height - c.offset + head;
        const room = iv.end - (y + gap);
        // Keep with next: a heading that fits but would end this interval alone moves on
        // with the start of the block it introduces. Not at the top of the column, where
        // moving can't help (so layout always makes progress).
        const strand = remaining <= room + EPS && c.b.keepWithNext && (usedInInterval || y > startY + EPS)
          && (leadHeight(cursors, qi, geo.blockGap) ?? 0) > room + EPS;
        if (strand) {
          y = iv.end + geo.blockGap;
          usedInInterval = false;
          if (iv.end >= limit - EPS) break; // column exhausted
          continue;
        }
        if (remaining <= room + EPS) {
          if (!dryRun) place(pb, c, x, y + gap, colW, remaining, c.b.height);
          y = y + gap + remaining;
          bottom = Math.max(bottom, y);
          usedInInterval = true;
          qi++;
          continue;
        }
        const s = c.b.kind === "text" ? bestSplit(c, room) : null;
        if (s) {
          if (!dryRun) place(pb, c, x, y + gap, colW, s.end - c.offset + head, s.end);
          bottom = Math.max(bottom, y + gap + (s.end - c.offset + head));
          c.offset = s.next;
          release(); // a held figure opens the next interval, ahead of the rest of this text
          y = iv.end + geo.blockGap; // the rest continues below the blocker or in the next column
          usedInInterval = false;
          continue;
        }
        // Doesn't fit. A block that can't fit even an empty full-height column region
        // is placed anyway (clipped and flagged), so layout always makes progress.
        const emptyRegion = !usedInInterval && iv.start <= startY + EPS && iv.end >= limit - EPS;
        if (emptyRegion && limit >= contentH - EPS && startY <= EPS) {
          if (!dryRun) place(pb, c, x, y, colW, iv.end - y, c.b.height, true);
          bottom = Math.max(bottom, iv.end);
          qi++;
          break;
        }
        // A floating block steps aside: the text after it fills this gap, and the block
        // opens the next interval. One at a time, and once each, so figures keep their order.
        if (c.b.float && c.b.kind === "atomic" && !c.floated && !held && backfills(cursors[qi + 1])) {
          c.floated = true;
          held = c;
          cursors.splice(qi, 1);
          continue;
        }
        release();
        y = iv.end + geo.blockGap;
        usedInInterval = false;
        if (iv.end >= limit - EPS) break; // column exhausted
      }
      release();
    }
    return { qi, cursors, bottom };
  };

  let pb = newPage();
  let bandTop = 0;

  /** Would this full-width block fit on the current page, at or below the band? */
  const fullFits = (b: MeasuredBlock): boolean => {
    let y = bandTop;
    for (let guard = 0; guard < 100; guard++) {
      const iv = freeAt(pb, 0, contentW, y, contentH);
      if (!iv) return false;
      if (b.height <= iv.end - iv.start + EPS) return true;
      if (iv.end >= contentH - EPS) return false;
      y = iv.end + geo.blockGap;
    }
    return false;
  };

  /** A full-width block: place below the band at the first free full-width interval. */
  const placeFull = (b: MeasuredBlock) => {
    let c: Cursor = { b, offset: 0 };
    let y = bandTop;
    let guard = 0;
    while (guard++ < 500) {
      const iv = freeAt(pb, 0, contentW, y, contentH);
      const head = headOf(c);
      const remaining = c.b.height - c.offset + head;
      if (iv && remaining <= iv.end - iv.start + EPS) {
        place(pb, c, 0, iv.start, contentW, remaining, c.b.height);
        bandTop = iv.start + remaining + geo.blockGap;
        break;
      }
      const s = iv && c.b.kind === "text" ? bestSplit(c, iv.end - iv.start) : null;
      if (iv && s) {
        place(pb, c, 0, iv.start, contentW, s.end - c.offset + head, s.end);
        c = { b, offset: s.next };
        y = iv.end + geo.blockGap;
        continue;
      }
      if (iv && iv.end < contentH - EPS) {
        y = iv.end + geo.blockGap; // try below the blocking pin
        continue;
      }
      const pageEmpty = pb.items.every((it) => it.pinned) && bandTop <= EPS && c.offset === 0;
      if (pageEmpty && iv) {
        place(pb, c, 0, iv.start, contentW, iv.end - iv.start, c.b.height, true);
        bandTop = contentH;
        break;
      }
      pb = newPage();
      bandTop = 0;
      y = 0;
    }
  };

  /**
   * A band of column-span blocks. With `balance`, a band that fits on the page is balanced
   * across the columns. `heldFull` is a floating full-width block that stepped aside for
   * this band: it opens the next page if the band runs over, and follows the band if not.
   */
  const runBand = (list: MeasuredBlock[], balance: boolean, heldFull: MeasuredBlock | null = null) => {
    let queue: Cursor[] = list.map((x) => ({ b: x, offset: 0 }));
    let guard = 0;
    while (queue.length && guard++ < 500) {
      const trial = fillBand(pb, queue, bandTop, contentH, true);
      if (trial.qi >= queue.length && balance && n > 1) {
        // Everything fits on this page: balance the columns (find the lowest limit that still fits).
        let lo = bandTop;
        let hi = contentH;
        for (let k = 0; k < 24; k++) {
          const mid = (lo + hi) / 2;
          if (fillBand(pb, queue, bandTop, mid, true).qi >= queue.length) hi = mid;
          else lo = mid;
        }
        const res = fillBand(pb, queue, bandTop, Math.ceil(hi), false);
        bandTop = res.bottom + geo.blockGap;
        queue = [];
        break;
      }
      const res = fillBand(pb, queue, bandTop, contentH, false);
      if (res.qi >= queue.length) {
        bandTop = res.bottom + geo.blockGap;
        queue = [];
        break;
      }
      // Partial: continue the band on a new page from where we stopped.
      const progressed = res.qi > 0 || res.cursors[0].offset !== queue[0].offset;
      queue = res.cursors.slice(res.qi);
      if (!progressed && pb.items.every((it) => it.pinned) && bandTop <= EPS) {
        // Nothing fits even on an empty page (fully reserved): force progress.
        const c = queue.shift()!;
        place(pb, c, 0, 0, colW, Math.min(c.b.height - c.offset, contentH), c.b.height, true);
      }
      pb = newPage();
      bandTop = 0;
      if (heldFull) {
        placeFull(heldFull);
        heldFull = null;
      }
    }
    if (heldFull) placeFull(heldFull);
  };

  /** The lowest free slot of height h in [x, x+w) on this page, below everything placed there. */
  const bottomSlot = (pg: PageBuilder, x: number, w: number, h: number): number | null => {
    const hit = (r: Rect) => r.x < x + w - EPS && r.x + r.w > x + EPS;
    let floor = 0; // the lowest flowing item in the span
    for (const it of pg.items) if (!it.pinned && !pg.bottoms.has(it.id) && hit(it)) floor = Math.max(floor, it.y + it.h + geo.blockGap);
    let bottom = contentH;
    const blockers = pg.reserved.filter(hit).sort((a, b) => b.y - a.y);
    for (const r of blockers) {
      if (r.y < bottom + EPS && r.y + r.h > bottom - h - EPS) bottom = Math.min(bottom, r.y - geo.blockGap);
    }
    const y = bottom - h;
    return y >= floor - EPS && y >= -EPS ? Math.max(0, y) : null;
  };

  /** A bottom-anchored block: at the foot of the page the flow has reached (the next page if
   *  it doesn't fit under what is already there). Later blocks flow in the space above it. */
  const placeBottom = (b: MeasuredBlock) => {
    const w = b.full ? contentW : colW;
    const x = b.full ? 0 : lastCol * (colW + geo.colGap);
    const c: Cursor = { b, offset: 0 };
    const col = lastCol;
    for (let tries = 0; tries < 2; tries++) {
      const y = bottomSlot(pb, x, w, b.height);
      const fresh = pb.items.every((it) => it.pinned) && bandTop <= EPS;
      if (y != null || fresh) {
        const top = y ?? Math.max(0, contentH - b.height);
        place(pb, c, x, top, w, Math.min(b.height, contentH - top), b.height, y == null || b.height > contentH + EPS);
        pb.reserved.push({ x, y: top, w, h: Math.min(b.height, contentH - top) });
        pb.bottoms.add(b.id);
        lastCol = col;
        return;
      }
      pb = newPage();
      bandTop = 0;
    }
  };

  let i = 0;
  while (i < flowing.length) {
    const b = flowing[i];
    if (b.kind === "break") {
      pb = newPage();
      bandTop = 0;
      i++;
      continue;
    }
    if (b.anchor === "bottom") {
      placeBottom(b);
      i++;
      continue;
    }
    if (b.full) {
      // A floating full-width block that doesn't fit steps aside for the text after it (up to
      // the next heading, figure or full-width block), and opens the next page.
      let j = i + 1;
      if (b.float && b.kind === "atomic" && !fullFits(b) && !(pb.items.every((it) => it.pinned) && bandTop <= EPS)) {
        while (j < flowing.length && backfills({ b: flowing[j], offset: 0 })) j++;
      }
      if (j > i + 1) runBand(flowing.slice(i + 1, j), true, b);
      else placeFull(b);
      i = j;
      continue;
    }
    // A band: consecutive column-span blocks up to the next full-width block, anchored block or page break.
    let j = i;
    while (j < flowing.length && !flowing[j].full && flowing[j].kind !== "break" && flowing[j].anchor !== "bottom") j++;
    const followedByFull = j < flowing.length && flowing[j].full && flowing[j].anchor !== "bottom";
    runBand(flowing.slice(i, j), followedByFull);
    i = j;
  }

  // Pages implied by pins on later pages, and by "Add page" (minPages).
  const maxPinPage = pinned.reduce((m, b) => (Array.isArray(b.repeat) ? Math.max(m, ...b.repeat, b.pin!.page)
    : Math.max(m, b.pin!.page)), 0);
  while (body.length < Math.max(geo.minPages, maxPinPage)) newPage();

  for (const p of body) pages.push({ kind: "body", number: p.number, items: p.items, height: contentH });
  if (back.length) stackPage("back", back, geo.back?.align || "center");
  return { pages, blockPages, contentW, contentH, colW };
}
