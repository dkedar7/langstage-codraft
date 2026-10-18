import { describe, expect, it } from "vitest";
import { paginate, type Geometry, type MeasuredBlock } from "./paginate";

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

describe("paginate: single column flow", () => {
  it("stacks blocks with the inter-block gap", () => {
    const r = paginate([atom("a", 100), atom("b", 50)], geo());
    expect(r.pages).toHaveLength(1);
    expect(r.pages[0].items.map((i) => [i.id, i.y, i.h])).toEqual([["a", 0, 100], ["b", 110, 50]]);
  });

  it("moves an atomic block that doesn't fit to the next page", () => {
    const r = paginate([atom("a", 300), atom("b", 150)], geo());
    expect(r.pages).toHaveLength(2);
    expect(r.blockPages).toEqual({ a: [1], b: [2] });
    expect(r.pages[1].items[0].y).toBe(0);
  });

  it("splits a text block at a paragraph boundary and continues on the next page", () => {
    const r = paginate([atom("a", 200), text("t", [100, 100, 100])], geo());
    // 400 tall: a=200, gap 10 -> 190 left: first paragraph (100) fits, second doesn't.
    const [p1, p2] = r.pages;
    const f1 = p1.items.find((i) => i.id === "t")!;
    expect(f1).toMatchObject({ y: 210, h: 100, offset: 0, fragment: true, continued: false });
    const f2 = p2.items.find((i) => i.id === "t")!;
    expect(f2).toMatchObject({ y: 0, offset: 110, h: 210, continued: true });
  });

  it("flags an atomic block taller than a page as overflow instead of looping", () => {
    const r = paginate([atom("big", 900), atom("b", 20)], geo());
    expect(r.pages[0].items[0]).toMatchObject({ id: "big", overflow: true, h: 400 });
    expect(r.blockPages.b).toEqual([2]);
  });

  it("page-break forces a new page", () => {
    const r = paginate([atom("a", 10), { id: "br", kind: "break", role: "flow", full: false, height: 0 }, atom("b", 10)], geo());
    expect(r.blockPages).toEqual({ a: [1], b: [2] });
  });

  it("minPages adds blank pages (Add page)", () => {
    expect(paginate([atom("a", 10)], geo({ minPages: 3 })).pages).toHaveLength(3);
  });
});

describe("paginate: columns", () => {
  it("fills column-major", () => {
    const r = paginate([atom("a", 300), atom("b", 300), atom("c", 50)], geo({ columns: 2 }));
    // colW = (500-20)/2 = 240
    const items = r.pages[0].items;
    expect(items.map((i) => [i.id, i.x, i.y])).toEqual([["a", 0, 0], ["b", 260, 0], ["c", 260, 310]]);
    expect(items[0].w).toBe(240);
  });

  it("balances a band before a full-width block", () => {
    const blocks = [atom("a", 100), atom("b", 100), atom("full", 50, { full: true }), atom("c", 20)];
    const r = paginate(blocks, geo({ columns: 2 }));
    const by = Object.fromEntries(r.pages[0].items.map((i) => [i.id, i]));
    expect(by.a).toMatchObject({ x: 0, y: 0 });
    expect(by.b).toMatchObject({ x: 260, y: 0 }); // balanced: b goes to column 2
    expect(by.full).toMatchObject({ x: 0, y: 110, w: 500 });
    expect(by.c.y).toBe(170);
  });

  it("does not balance the last band (fills the first column first)", () => {
    const r = paginate([atom("a", 100), atom("b", 100)], geo({ columns: 2 }));
    expect(r.pages[0].items.map((i) => i.x)).toEqual([0, 0]);
  });

  it("splits text across columns", () => {
    const r = paginate([text("t", [150, 150, 150])], geo({ columns: 2 }));
    const frags = r.pages[0].items;
    expect(frags).toHaveLength(2);
    expect(frags[0]).toMatchObject({ x: 0, offset: 0, h: 310 });
    expect(frags[1]).toMatchObject({ x: 260, offset: 320, h: 150 });
  });
});

describe("paginate: pinned and repeated blocks", () => {
  it("reserves a pinned rectangle and flows around it", () => {
    const pin = atom("logo", 0, { pin: { page: 1, top: 0, column: 0, span: 1, height: 100 } });
    const r = paginate([pin, atom("a", 50)], geo());
    const a = r.pages[0].items.find((i) => i.id === "a")!;
    expect(a.y).toBe(110); // below the reservation + gap
    expect(r.pages[0].items.find((i) => i.id === "logo")).toMatchObject({ pinned: true, y: 0, h: 100 });
  });

  it("fills the gap above a pin placed mid-column, then continues below it", () => {
    const pin = atom("fig", 0, { pin: { page: 1, top: 150, column: 0, span: 1, height: 100 } });
    const r = paginate([pin, atom("a", 100), atom("b", 100)], geo());
    const by = Object.fromEntries(r.pages[0].items.map((i) => [i.id, i]));
    expect(by.a.y).toBe(0); // 0..100 fits above 150-10
    expect(by.b.y).toBe(260); // can't fit 110..140, goes below the pin (250 + gap)
  });

  it("a pin only blocks its own columns", () => {
    const pin = atom("side", 0, { pin: { page: 1, top: 0, column: 1, span: 1, height: 400 } });
    const r = paginate([pin, atom("a", 300), atom("b", 300)], geo({ columns: 2 }));
    expect(r.blockPages).toMatchObject({ a: [1], b: [2] }); // column 2 is fully reserved
  });

  it("repeats a pinned header on every page and adds pages for later pins", () => {
    const header = atom("hdr", 0, { pin: { page: 1, top: 0, column: 0, span: 1, height: 40 }, repeat: "all" });
    const late = atom("late", 0, { pin: { page: 3, top: 100, column: 0, span: 1, height: 50 } });
    const r = paginate([header, late, atom("a", 50)], geo());
    expect(r.pages).toHaveLength(3);
    expect(r.blockPages.hdr).toEqual([1, 2, 3]);
    expect(r.pages[1].items.find((i) => i.id === "hdr")!.repeated).toBe(true);
    expect(r.blockPages.late).toEqual([3]);
  });

  it("odd/even repeat", () => {
    const f = atom("f", 0, { pin: { page: 1, top: 380, column: 0, span: 1, height: 20 }, repeat: "even" });
    const r = paginate([f, atom("a", 300), atom("b", 300), atom("c", 300)], geo());
    expect(r.blockPages.f).toEqual([2]);
  });
});

describe("paginate: cover, back, pageless", () => {
  it("cover and back blocks get their own single-column pages, centered", () => {
    const blocks = [atom("title", 100, { role: "cover" }), atom("a", 10), atom("thanks", 40, { role: "back" })];
    const r = paginate(blocks, geo({ columns: 3 }));
    expect(r.pages.map((p) => p.kind)).toEqual(["cover", "body", "back"]);
    expect(r.pages[0].items[0]).toMatchObject({ y: 150, w: 500 });
    expect(r.blockPages.a).toEqual([2]);
  });

  it("repeated pins skip cover pages", () => {
    const hdr = atom("hdr", 0, { pin: { page: 1, top: 0, column: 0, span: 1, height: 40 }, repeat: "all" });
    const r = paginate([atom("cov", 50, { role: "cover" }), hdr, atom("a", 10)], geo());
    expect(r.blockPages.hdr).toEqual([2]);
  });

  it("pageless stacks everything in one tall page", () => {
    const r = paginate([atom("a", 300), atom("b", 300), { id: "br", kind: "break", role: "flow", full: false, height: 0 }],
      geo({ mode: "pageless" }));
    expect(r.pages).toHaveLength(1);
    expect(r.pages[0].height).toBe(610);
  });
});

describe("paginate: determinism", () => {
  it("is a pure function", () => {
    const blocks = [text("t", [120, 80, 200, 60]), atom("x", 90, { full: true }), atom("y", 130)];
    const g = geo({ columns: 3 });
    expect(JSON.stringify(paginate(blocks, g))).toBe(JSON.stringify(paginate(blocks, g)));
  });
});
