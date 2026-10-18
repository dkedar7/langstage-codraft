import { describe, expect, it } from "vitest";
import { pageNumbers, usesPageToken } from "./numbering";

const pages = [
  { kind: "cover" as const, number: 1 },
  { kind: "body" as const, number: 2 },
  { kind: "body" as const, number: 3 },
  { kind: "body" as const, number: 4 },
  { kind: "back" as const, number: 5 },
];

describe("pageNumbers", () => {
  it("skips cover and back pages by default", () => {
    const r = pageNumbers(pages, { start: 1, numberCoverPages: false, unnumbered: [] });
    expect(r.map).toEqual({ 1: null, 2: 1, 3: 2, 4: 3, 5: null });
    expect(r.count).toBe(3);
    expect(r.last).toBe(3);
  });

  it("can number every page", () => {
    const r = pageNumbers(pages, { start: 1, numberCoverPages: true, unnumbered: [] });
    expect(r.map).toEqual({ 1: 1, 2: 2, 3: 3, 4: 4, 5: 5 });
  });

  it("honors the starting number and unnumbered pages", () => {
    const r = pageNumbers(pages, { start: 68, numberCoverPages: false, unnumbered: [3] });
    expect(r.map).toEqual({ 1: null, 2: 68, 3: null, 4: 69, 5: null });
    expect(r.last).toBe(69);
  });

  it("detects page-number blocks", () => {
    expect(usesPageToken({ markdown: "**{page}** JULY" })).toBe(true);
    expect(usesPageToken({ markdown: "{pages} total" })).toBe(false);
  });
});
