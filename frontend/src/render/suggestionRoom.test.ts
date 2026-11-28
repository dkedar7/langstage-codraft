import { describe, expect, it } from "vitest";
import { SUGGESTION_BAR_ROOM, toMeasuredBlocks } from "./useDocLayout";
import type { Block } from "../types";

const block = (id: string, extra: Partial<Block> = {}): Block => ({
  id, doc_id: "d", type: "text", ord: 0, content: { markdown: "x" }, layout: {}, ...extra,
} as Block);

describe("room for a suggested block's Accept / Reject bar (gh #78)", () => {
  const m = { a: { height: 40, natural: 40, splits: [] }, v: { height: 40, natural: 40, splits: [] } };

  it("keeps room below a pending insert, and only there", () => {
    const [a, v] = toMeasuredBlocks([block("a"), block("v", { virtual: { suggestionId: "s" } })], m);
    expect(a.height).toBe(40);
    expect(v.height).toBe(40 + SUGGESTION_BAR_ROOM);
  });

  it("leaves a fixed-height or pinned suggestion's box as it is", () => {
    const fixed = block("v", { virtual: { suggestionId: "s" }, layout: { height: 100 } as Block["layout"] });
    expect(toMeasuredBlocks([fixed], m)[0].height).toBe(100);
  });
});
