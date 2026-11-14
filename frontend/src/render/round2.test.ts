import { describe, expect, it } from "vitest";
import type { Block, Ref } from "../types";
import { chartSpec, labelPadding, orderStack } from "./chartSpec";
import { cellHasMarkup, htmlToMd, renderMarkdown } from "./markdown";

// The sanitizer needs a DOM; these check the Markdown rendering it is given.
const mdToHtml = (md: string, opts: { cites?: Record<string, number> } = {}) => renderMarkdown(md, opts.cites ?? null);
const cellToHtml = (md: string, opts: { cites?: Record<string, number> } = {}) => renderMarkdown(md, opts.cites ?? null, true);
import { citeKeys, citeNumbers, compileReferences, refParts, replaceCites } from "./refs";

const ref = (r: Partial<Ref>): Ref => ({ title: "", url: "", note: "", ...r });
const block = (id: string, markdown: string, refs: Ref[] = [], type: Block["type"] = "text", content?: Record<string, any>): Block => ({
  id, doc_id: "d", type, ord: 0, content: content ?? { markdown }, refs, head: "", n_versions: 1, author: "user", updated_at: 0,
  layout: { span: "column", height: null, role: "flow", pin: null, repeat: null },
});

describe("references: numbered in order of first appearance", () => {
  const a = ref({ key: "rivera2024", title: "Fleet costs", authors: "Rivera, A.", year: "2024", url: "https://example.org/a" });
  const b = ref({ key: "chen2023", title: "Charging", authors: "Chen, B.", year: "2023" });
  const c = ref({ key: "acme", title: "Acme handbook", note: "internal: check the 2025 edition" });

  it("numbers a source where the text first cites it, then where its block stands", () => {
    // Block 1 carries a and b but cites b first; block 2 cites c before carrying it.
    const blocks = [block("one", "Costs fell [@chen2023], as [@rivera2024] found.", [a, b]), block("two", "See [@acme].", [c])];
    expect(compileReferences(blocks).map((r) => [r.n, r.key])).toEqual([[1, "chen2023"], [2, "rivera2024"], [3, "acme"]]);
    expect(citeNumbers(blocks)).toEqual({ chen2023: 1, rivera2024: 2, acme: 3 });
  });

  it("a source cited from an earlier block is numbered there, though a later block carries it", () => {
    const blocks = [block("one", "First [@acme]."), block("two", "Then [@rivera2024].", [a]), block("three", "", [c])];
    expect(compileReferences(blocks).map((r) => r.key)).toEqual(["acme", "rivera2024"]);
  });

  it("lists an uncited source after the cited ones of its block, and each source once", () => {
    const blocks = [block("one", "Cited [@chen2023].", [a, b]), block("two", "", [{ ...a, key: "dup" }])];
    expect(compileReferences(blocks).map((r) => r.key)).toEqual(["chen2023", "rivera2024"]);
    // the duplicate's key still resolves to the source's number
    expect(citeNumbers(blocks).dup).toBe(2);
  });

  it("reads citations in table cells and row cells", () => {
    const t = block("t", "", [a], "table", { rows: [["Item", "Source"], ["Vans", "[@rivera2024]"]] });
    const r = block("r", "", [b], "row", { cells: [{ markdown: "One [@chen2023]" }, { markdown: "Two" }] });
    expect(compileReferences([r, t]).map((x) => x.key)).toEqual(["chen2023", "rivera2024"]);
    expect(citeKeys("a [@x; @y] b [@z]")).toEqual(["x", "y", "z"]);
  });

  it("prints Authors (Year). Title. Publisher. and never the private note", () => {
    expect(refParts({ ...a, publisher: "Example Press", accessed: "2026-01-05" })).toEqual({
      text: "Rivera, A. (2024). Fleet costs. Example Press.", url: "https://example.org/a", accessed: "Accessed 2026-01-05." });
    expect(refParts(c)).toEqual({ text: "Acme handbook.", url: "", accessed: "" });
    expect(refParts(ref({ title: "Report", year: "2022" })).text).toBe("Report (2022).");
    // a source known only by its address is the link alone
    expect(refParts(ref({ title: "https://example.org/x", url: "https://example.org/x" }))).toEqual({
      text: "", url: "https://example.org/x", accessed: "" });
    expect(JSON.stringify(refParts(c))).not.toContain("internal");
  });
});

describe("citations in Markdown", () => {
  const cites = { rivera2024: 2, chen2023: 1 };

  it("[@key] prints as its number, linked to the list entry", () => {
    const html = mdToHtml("Costs fell [@rivera2024].", { cites });
    expect(html).toContain('[<a class="cd-cite" href="#cd-ref-2">2</a>]');
    expect(mdToHtml("Both [@chen2023; @rivera2024].", { cites })).toContain(
      '[<a class="cd-cite" href="#cd-ref-1">1</a>, <a class="cd-cite" href="#cd-ref-2">2</a>]');
  });

  it("marks a key no source has, and leaves code and plain brackets alone", () => {
    expect(mdToHtml("Odd [@nobody].", { cites })).toContain("cd-cite-missing");
    expect(mdToHtml("Use `[@rivera2024]` to cite.", { cites })).toContain("<code>[@rivera2024]</code>");
    expect(mdToHtml("An index [1] and an email a@example.org.", { cites })).toContain("[1]");
  });

  it("is left as written when the caller gives no numbers", () => {
    expect(mdToHtml("Costs fell [@rivera2024].")).toContain("[@rivera2024]");
  });

  it("survives the rich-text editor round trip", () => {
    expect(htmlToMd("<p>Costs fell [@rivera2024] and [@a_b; @c].</p>")).toBe("Costs fell [@rivera2024] and [@a_b; @c].");
    expect(replaceCites("x [@chen2023; @none]", cites)).toBe("x [1, ?]");
  });
});

describe("table cells are one line of Markdown", () => {
  it("renders bold, italic, code, links, superscript and subscript", () => {
    expect(cellToHtml("**Total**")).toBe("<strong>Total</strong>");
    expect(cellToHtml("*net* `kWh` [docs](https://example.org) m^2^ CO~2~")).toBe(
      '<em>net</em> <code>kWh</code> <a href="https://example.org">docs</a> m<sup>2</sup> CO<sub>2</sub>');
    expect(cellToHtml("See [@k]", { cites: { k: 4 } })).toContain('href="#cd-ref-4"');
  });

  it("does not wrap the cell in a paragraph", () => {
    expect(cellToHtml("plain")).toBe("plain");
  });

  it("plain text takes the short path", () => {
    expect(cellHasMarkup("$27.68m")).toBe(false);
    expect(cellHasMarkup("Sev 4, next day (est.)")).toBe(false);
    expect(cellHasMarkup("**Total**")).toBe(true);
    expect(cellHasMarkup("R&D")).toBe(true);
  });
});

describe("charts: the stack follows the legend", () => {
  const values = [{ s: "A", part: "Vehicles", v: 3 }, { s: "A", part: "Chargers", v: 1 }];
  const color = { field: "part", type: "nominal", sort: ["Vehicles", "Chargers", "Fuel"] };

  it("a horizontal stack with a listed order runs left to right in that order", () => {
    const out = orderStack({ mark: { type: "bar" }, data: { values },
      encoding: { y: { field: "s", type: "nominal" }, x: { field: "v", type: "quantitative", stack: "zero" }, color } });
    expect(out.encoding.order).toEqual({ field: "__cd_order", type: "quantitative", sort: "ascending" });
    expect(out.transform.at(-1)).toEqual({ calculate: 'indexof(["Vehicles","Chargers","Fuel"], datum["part"])', as: "__cd_order" });
  });

  it("a vertical stack with a listed order reads top to bottom like the legend", () => {
    const out = orderStack({ mark: "bar", encoding: { x: { field: "s", type: "nominal" }, y: { field: "v", type: "quantitative" }, color } });
    expect(out.encoding.order.sort).toBe("descending");
  });

  it("a horizontal stack without a listed order follows the legend's own (ascending) order", () => {
    const out = orderStack({ mark: "bar", encoding: { y: { field: "s", type: "nominal" }, x: { field: "v", type: "quantitative" },
      color: { field: "part", type: "nominal" } } });
    expect(out.encoding.order).toEqual({ field: "part", type: "nominal", sort: "ascending" });
  });

  it("leaves alone a spec with its own order, an unstacked chart, a vertical default and other marks", () => {
    const enc = { y: { field: "s", type: "nominal" }, x: { field: "v", type: "quantitative" }, color };
    const own = { mark: "bar", encoding: { ...enc, order: { field: "v" } } };
    expect(orderStack(own)).toBe(own);
    const flat = { mark: "bar", encoding: { ...enc, x: { ...enc.x, stack: null } } };
    expect(orderStack(flat)).toBe(flat);
    const vertical = { mark: "bar", encoding: { x: { field: "s", type: "nominal" }, y: { field: "v", type: "quantitative" },
      color: { field: "part", type: "nominal" } } };
    expect(orderStack(vertical)).toBe(vertical);
    const line = { mark: "line", encoding: enc };
    expect(orderStack(line)).toBe(line);
    expect(chartSpec({ mark: "bar", data: { values }, encoding: enc }, 400, 200).encoding.order.field).toBe("__cd_order");
  });
});

describe("charts: room for labels past the plot", () => {
  const data = { values: [{ m: "Share", v: 70, label: ">70% of heating demand" }, { m: "Count", v: 30, label: ">30" }] };
  const bars = { mark: "bar", encoding: { y: { field: "m", type: "nominal" }, x: { field: "v", type: "quantitative" } } };
  const labels = { mark: { type: "text", align: "left", dx: 6, fontSize: 12 },
    encoding: { y: { field: "m", type: "nominal" }, x: { field: "v", type: "quantitative" }, text: { field: "label", type: "nominal" } } };

  it("pads the right for value labels after horizontal bars", () => {
    const pad = labelPadding({ data, layer: [bars, labels] }, 600)!;
    expect(pad.right).toBeGreaterThan(120);
    expect(pad.right).toBeLessThanOrEqual(240);
    const full = chartSpec({ data, layer: [bars, labels] }, 600, 220);
    expect(full.padding.right).toBe(5 + pad.right);
  });

  it("pads the top for labels above vertical bars, and nothing without text marks", () => {
    const up = { mark: { type: "text", dy: -4 }, encoding: { x: { field: "m", type: "nominal" }, y: { field: "v", type: "quantitative" },
      text: { field: "label" } } };
    expect(labelPadding({ data, layer: [up] }, 400)).toEqual({ right: 0, top: 19 });
    expect(labelPadding({ data, ...bars }, 400)).toBeNull();
    expect(chartSpec({ data, ...bars }, 400, 200).padding).toBeUndefined();
  });

  it("keeps a padding the spec sets itself", () => {
    expect(chartSpec({ data, layer: [bars, labels], padding: 2 }, 600, 220).padding).toBe(2);
  });
});
