import { describe, expect, it } from "vitest";
import { blockFill, resolveFill, rowCells, rowColumns, rowGap, statColumns, statItems } from "./layoutBlocks";
import { MM } from "../layout/units";

const THEME = { color_accent: "#ff5a1f", color_text: "#111111", color_callout: "#fff4e6", color_muted: "#666" };

describe("row cells", () => {
  it("reads Markdown cells and always has at least two", () => {
    expect(rowCells({ cells: [{ markdown: "a" }, "b", { markdown: 3 }, null] })).toEqual(["a", "b", "", ""]);
    expect(rowCells({ cells: "nope" })).toEqual(["", ""]);
    expect(rowCells(undefined)).toEqual(["", ""]);
    expect(rowCells({ cells: ["1", "2", "3", "4", "5"] })).toHaveLength(4);
  });

  it("uses relative widths only when there is one valid number per cell", () => {
    expect(rowColumns([1, 1, 2], 3)).toBe("minmax(0, 1fr) minmax(0, 1fr) minmax(0, 2fr)");
    expect(rowColumns(null, 3)).toBe("repeat(3, minmax(0, 1fr))");
    expect(rowColumns([1, 2], 3)).toBe("repeat(3, minmax(0, 1fr))");        // left over from a row with two cells
    expect(rowColumns([1, 0, 2], 3)).toBe("repeat(3, minmax(0, 1fr))");
    expect(rowColumns(["1", "2"], 2)).toBe("repeat(2, minmax(0, 1fr))");
  });

  it("takes its own gap in mm, else the document's column gap", () => {
    expect(rowGap({ gap: 5 }, 30)).toBeCloseTo(5 * MM);
    expect(rowGap({ gap: 0 }, 30)).toBe(0);
    expect(rowGap({ gap: null }, 30)).toBe(30);
    expect(rowGap({ gap: "wide" }, undefined)).toBe(24);
  });
});

describe("number tiles", () => {
  it("reads values and labels as text and drops what isn't a tile", () => {
    expect(statItems({ items: [{ value: 42, label: "answers" }, "junk", { value: "38 ms" }, ["a", "b"]] }))
      .toEqual([{ value: "42", label: "answers" }, { value: "38 ms", label: "" }]);
    expect(statItems({ items: [] })).toEqual([{ value: "", label: "" }]);
    expect(statItems({})).toEqual([{ value: "", label: "" }]);
  });

  it("keeps tiles on one line while they fit, else splits them over two", () => {
    expect(statColumns(3, 624, 18)).toBe(3);
    expect(statColumns(6, 624, 18)).toBe(3);    // 6 tiles of ~89px would be too narrow
    expect(statColumns(4, 300, 18)).toBe(2);
    expect(statColumns(1, 80, 18)).toBe(1);
  });
});

describe("fill", () => {
  it("resolves a theme colour name or a hex colour, and says when the text must be light", () => {
    expect(resolveFill("accent", THEME)).toEqual({ color: "#ff5a1f", dark: true });
    expect(resolveFill("callout", THEME)).toEqual({ color: "#fff4e6", dark: false });
    expect(resolveFill("#1d3557", THEME)).toEqual({ color: "#1d3557", dark: true });
    expect(resolveFill("#fff", THEME)).toEqual({ color: "#fff", dark: false });
    expect(resolveFill("heading", THEME)).toEqual({ color: "#111111", dark: true });   // falls back to the text colour
    expect(resolveFill("figure", THEME)).toEqual({ color: "#fff4e6", dark: false });   // falls back to the callout tint
    expect(resolveFill("rule", THEME)).toBeNull();
    expect(resolveFill("orange", THEME)).toBeNull();
    expect(resolveFill(null, THEME)).toBeNull();
  });

  it("gives a band the accent when it names no fill, and ignores a fill on other block types", () => {
    const layout = (l: object) => ({ span: "column", height: null, role: "flow", pin: null, repeat: null, ...l }) as any;
    expect(blockFill({ type: "heading", layout: layout({ variant: "band" }) }, THEME)).toEqual({ color: "#ff5a1f", dark: true });
    expect(blockFill({ type: "callout", layout: layout({ variant: "band", fill: "#fff4e6" }) }, THEME)?.dark).toBe(false);
    expect(blockFill({ type: "stat", layout: layout({ fill: "text" }) }, THEME)).toEqual({ color: "#111111", dark: true });
    expect(blockFill({ type: "row", layout: layout({ variant: "cards" }) }, THEME)).toBeNull();   // cards use the theme tint
    expect(blockFill({ type: "heading", layout: layout({}) }, THEME)).toBeNull();
    expect(blockFill({ type: "text", layout: layout({ fill: "accent" }) }, THEME)).toBeNull();
  });
});
