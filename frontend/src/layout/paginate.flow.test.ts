import { describe, expect, it } from "vitest";
import { paginate, type Geometry, type MeasuredBlock } from "./paginate";

// Tables that split, figures that step aside, and blocks anchored to the bottom of a page.

const geo = (over: Partial<Geometry> = {}): Geometry => ({
  mode: "paged",
  pageW: 600,
  pageH: 500,
  margin: { top: 50, right: 50, bottom: 50, left: 50 }, // content 500 x 400
  columns: 1,
  colGap: 20,
  blockGap: 10,
  minPages: 1,
  ...over,
});

const atom = (id: string, height: number, extra: Partial<MeasuredBlock> = {}): MeasuredBlock => ({
  id, kind: "atomic", role: "flow", full: false, height, ...extra,
});

/** A text block made of paragraphs of the given heights, 10px apart. */
const text = (id: string, paras: number[], extra: Partial<MeasuredBlock> = {}): MeasuredBlock => {
  const splits = [];
  let y = 0;
  for (let k = 0; k < paras.length; k++) {
    y += paras[k];
    if (k < paras.length - 1) splits.push({ end: y, next: y + 10 });
    y += k < paras.length - 1 ? 10 : 0;
  }
  return { id, kind: "text", role: "flow", full: false, height: y, splits, ...extra };
};

/** A table: a 30px header row and `rows` body rows of 20px. Splits keep two rows on each side. */
const table = (id: string, rows: number, extra: Partial<MeasuredBlock> = {}): MeasuredBlock => {
  const splits = [];
  for (let k = 2; k <= rows - 2; k++) splits.push({ end: 30 + k * 20, next: 30 + k * 20 });
  return { id, kind: "text", role: "flow", full: false, height: 30 + rows * 20, splits, head: 30, ...extra };
};

/** Where a block's pieces landed: [page, y, h, offset, head]. */
const pieces = (r: ReturnType<typeof paginate>, id: string) =>
  r.pages.flatMap((p) => p.items.filter((i) => i.id === id).map((i) => [p.number, i.y, i.h, i.offset, i.head ?? 0]));

describe("paginate: a table splits between rows and repeats its header", () => {
  it("fills the page and continues with the header row on the next", () => {
    // 100 used; the 290 left hold the header (30) and 13 rows (260). 20 rows in all.
    const r = paginate([atom("a", 100), table("t", 20)], geo());
    expect(pieces(r, "t")).toEqual([[1, 110, 290, 0, 0], [2, 0, 30 + 7 * 20, 290, 30]]);
    expect(r.blockPages.t).toEqual([1, 2]);
  });

  it("never leaves a lone header or a single row behind", () => {
    // 60 left: the header and one row would fit, but a split keeps two rows on each side.
    const r = paginate([atom("a", 330), table("t", 10)], geo());
    expect(pieces(r, "t")).toEqual([[2, 0, 230, 0, 0]]);
    // 19 rows fit a page with the header (30 + 18.5 rows): the split steps back so two rows continue.
    const s = paginate([table("t", 19)], geo());
    const ps = pieces(s, "t");
    expect(ps).toHaveLength(2);
    expect(ps[1][2] - 30).toBeGreaterThanOrEqual(40);
  });

  it("counts the repeated header against the room of every continued piece", () => {
    const r = paginate([table("t", 60)], geo());
    const ps = pieces(r, "t");
    expect(ps.length).toBeGreaterThan(3);
    for (const [, y, h, offset, head] of ps) {
      expect(y + h).toBeLessThanOrEqual(400);
      expect(head).toBe(offset > 0 ? 30 : 0);
    }
    // every row is shown exactly once: the pieces' own rows add up to the table
    const shown = ps.reduce((sum, [, , h, , head]) => sum + h - head, 0);
    expect(shown).toBe(30 + 60 * 20);
  });

  it("splits a full-width table too, and across columns", () => {
    const full = paginate([atom("a", 100), table("t", 20, { full: true })], geo());
    expect(pieces(full, "t").map((p) => p[0])).toEqual([1, 2]);
    const cols = paginate([table("t", 30)], geo({ columns: 2 }));
    const items = cols.pages[0].items.filter((i) => i.id === "t");
    expect(items.map((i) => i.x)).toEqual([0, 260]);
    expect(items[1].head).toBe(30);
  });

  it("leaves a table without splits atomic, as before", () => {
    const r = paginate([atom("a", 300), atom("t", 150)], geo());
    expect(r.blockPages).toEqual({ a: [1], t: [2] });
  });
});

describe("paginate: a floating block lets text back-fill the gap", () => {
  it("is off unless the block floats: the gap stays and nothing moves", () => {
    const r = paginate([atom("a", 200), atom("fig", 250), text("t", [100, 100, 100])], geo());
    expect(pieces(r, "fig")).toEqual([[2, 0, 250, 0, 0]]);
    expect(r.blockPages.t).toEqual([2, 3]);
  });

  it("text fills the gap and the figure opens the next page", () => {
    const r = paginate([atom("a", 200), atom("fig", 250, { float: true }), text("t", [100, 100, 100])], geo());
    expect(pieces(r, "t")).toEqual([[1, 210, 100, 0, 0], [2, 260, 100, 110, 0], [3, 0, 100, 220, 0]]);
    expect(pieces(r, "fig")).toEqual([[2, 0, 250, 0, 0]]);
  });

  it("does not float when the figure fits", () => {
    const r = paginate([atom("a", 100), atom("fig", 250, { float: true }), text("t", [100])], geo());
    expect(pieces(r, "fig")).toEqual([[1, 110, 250, 0, 0]]);
  });

  it("only text up to the next heading moves ahead", () => {
    const h = atom("h", 30, { keepWithNext: true });
    const r = paginate([atom("a", 200), atom("fig", 250, { float: true }), text("t", [50]), h, text("u", [100, 100])], geo());
    expect(r.blockPages).toMatchObject({ t: [1], fig: [2], h: [2], u: [2, 3] });
    expect(r.pages[1].items.map((i) => i.id)).toEqual(["fig", "h", "u"]);
  });

  it("keeps two figures in order: the second never passes the first", () => {
    const r = paginate([atom("a", 200), atom("f1", 250, { float: true }), atom("f2", 250, { float: true }),
      text("t", [100, 100])], geo());
    const order = r.pages.flatMap((p) => p.items.map((i) => i.id)).filter((id) => id === "f1" || id === "f2");
    expect(order).toEqual(["f1", "f2"]);
    expect(r.blockPages.f1[0]).toBeLessThan(r.blockPages.f2[0]);
  });

  it("a heading stays with the text when the figure after it steps aside", () => {
    const h = atom("h", 30, { keepWithNext: true });
    const r = paginate([atom("a", 200), h, atom("fig", 250, { float: true }), text("t", [100, 100, 100])], geo());
    expect(r.blockPages.h).toEqual([1]);
    expect(pieces(r, "t")[0]).toEqual([1, 250, 100, 0, 0]);
    expect(pieces(r, "fig")).toEqual([[2, 0, 250, 0, 0]]);
  });

  it("a full-width figure steps aside for the column text after it, and the band is balanced", () => {
    const r = paginate([atom("a", 200, { full: true }), atom("fig", 250, { full: true, float: true }),
      text("t", [60, 60]), text("u", [60])], geo({ columns: 2 }));
    expect(r.blockPages).toMatchObject({ t: [1], u: [1], fig: [2] });
    expect(pieces(r, "fig")).toEqual([[2, 0, 250, 0, 0]]);
    expect(r.pages[0].items.find((i) => i.id === "u")!.x).toBe(260);
  });

  it("a full-width figure opens the next page when the text runs over", () => {
    const r = paginate([atom("a", 200, { full: true }), atom("fig", 250, { full: true, float: true }),
      text("t", [100, 100, 100, 100])], geo());
    expect(r.pages[1].items.map((i) => i.id)).toEqual(["fig", "t"]);
    expect(pieces(r, "t")[0]).toEqual([1, 210, 100, 0, 0]);
  });

  it("is deterministic and places every block once", () => {
    const blocks = [atom("a", 200), atom("f1", 250, { float: true }), text("t", [80, 80, 80]), atom("f2", 300, { float: true }),
      text("u", [120, 120]), atom("f3", 90, { float: true })];
    const one = paginate(blocks, geo({ columns: 2 }));
    expect(paginate(blocks, geo({ columns: 2 }))).toEqual(one);
    for (const b of blocks) expect(one.blockPages[b.id]?.length).toBeGreaterThan(0);
    for (const id of ["f1", "f2", "f3", "a"]) expect(pieces(one, id)).toHaveLength(1);
  });
});

describe("paginate: a block anchored to the bottom of the page", () => {
  it("sits at the foot of the page, and later blocks flow above it", () => {
    const r = paginate([atom("a", 100), atom("cta", 80, { full: true, anchor: "bottom" }), atom("b", 50)], geo());
    expect(pieces(r, "cta")).toEqual([[1, 320, 80, 0, 0]]);
    expect(pieces(r, "b")).toEqual([[1, 110, 50, 0, 0]]);
  });

  it("goes to the next page when there is no room under what is already placed", () => {
    const r = paginate([atom("a", 350), atom("cta", 80, { anchor: "bottom" })], geo());
    expect(pieces(r, "cta")).toEqual([[2, 320, 80, 0, 0]]);
  });

  it("text flowing after it stops above it and continues on the next page", () => {
    const r = paginate([atom("cta", 100, { anchor: "bottom" }), text("t", [100, 100, 100, 100])], geo());
    expect(pieces(r, "t")).toEqual([[1, 0, 210, 0, 0], [2, 0, 210, 220, 0]]);
    expect(pieces(r, "cta")).toEqual([[1, 300, 100, 0, 0]]);
  });

  it("stacks above a pinned footer and above an earlier anchored block", () => {
    const footer = atom("f", 0, { pin: { page: 1, top: 370, column: 0, span: 1, height: 30 } });
    const r = paginate([footer, atom("x", 60, { anchor: "bottom" }), atom("y", 40, { anchor: "bottom" })], geo());
    expect(pieces(r, "x")).toEqual([[1, 300, 60, 0, 0]]);
    expect(pieces(r, "y")).toEqual([[1, 250, 40, 0, 0]]);
  });

  it("a column-span one sits under the column the flow is in", () => {
    const r = paginate([atom("a", 300), atom("b", 100), atom("note", 50, { anchor: "bottom" })], geo({ columns: 2 }));
    expect(r.pages[0].items.find((i) => i.id === "note")).toMatchObject({ x: 260, y: 350, w: 240 });
  });

  it("is flagged when it is taller than the page", () => {
    const r = paginate([atom("big", 900, { anchor: "bottom" })], geo());
    expect(r.pages[0].items[0]).toMatchObject({ id: "big", overflow: true });
  });
});

describe("paginate: a pinned block whose content is taller than its box", () => {
  it("is flagged as overflow", () => {
    const pin = { page: 1, top: 0, column: 0, span: 1, height: 100 };
    const r = paginate([atom("p", 100, { pin, natural: 180 }), atom("q", 100, { pin: { ...pin, top: 200 }, natural: 90 })], geo());
    expect(r.pages[0].items.find((i) => i.id === "p")!.overflow).toBe(true);
    expect(r.pages[0].items.find((i) => i.id === "q")!.overflow).toBe(false);
  });

  it("a page-anchored pin may bleed into the bottom margin; past the page edge it overflows", () => {
    const pin = (top: number) => ({ anchor: "page" as const, page: 1, top, x: 50, w: 300, column: 0, span: 1, height: 100 });
    const r = paginate([atom("bleed", 100, { pin: pin(380), clips: true }), atom("off", 100, { pin: pin(420), clips: true })], geo());
    expect(r.pages[0].items.find((i) => i.id === "bleed")!.overflow).toBe(false);   // ends at 480 of a 500px page
    expect(r.pages[0].items.find((i) => i.id === "off")!.overflow).toBe(true);      // ends at 520
  });

  it("a text pin past any edge of the page is off the page (gh #72)", () => {
    const g = geo();
    const at = (x: number, top: number) => ({ anchor: "page" as const, page: 1, top, x, w: 100, column: 0, span: 1, height: 50 });
    const r = paginate([
      atom("left", 50, { pin: at(-2000, 100), clips: true }),
      atom("top", 50, { pin: at(50, -2000), clips: true }),
      atom("right", 50, { pin: at(g.pageW - 50, 100), clips: true }),
      atom("inside", 50, { pin: at(0, 0), clips: true }),
    ], g);
    const it = (id: string) => r.pages[0].items.find((i) => i.id === id)!;
    for (const id of ["left", "top", "right"]) {
      expect(it(id).overflow).toBe(true);
      expect(it(id).offPage).toBe(true);
    }
    expect(it("inside").overflow).toBe(false);
    expect(it("inside").offPage).toBeFalsy();
  });

  it("an image pin may bleed past the edge while at least half of it is on the page (gh #72)", () => {
    const at = (x: number) => ({ anchor: "page" as const, page: 1, top: 100, x, w: 100, column: 0, span: 1, height: 50 });
    const r = paginate([atom("bleed", 50, { pin: at(-20) }), atom("mostly-off", 50, { pin: at(-80) })], geo());
    expect(r.pages[0].items.find((i) => i.id === "bleed")!.overflow).toBe(false);
    expect(r.pages[0].items.find((i) => i.id === "mostly-off")!.offPage).toBe(true);
  });
});
