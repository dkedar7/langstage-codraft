import { describe, expect, it } from "vitest";
import { blockName, describeCall, groupCalls, summarizeCalls, toolLabel } from "./toolSummary";

const blocks = [
  { id: "blk_1", type: "heading", content: { text: "Executive summary", level: 2 } },
  { id: "blk_2", type: "text", content: { markdown: "A **phased** electrification of about 80 vans a year is the strongest option." } },
  { id: "blk_3", type: "chart", content: { caption: "", spec: {} } },
];
const look = { blocks, uploads: [{ id: "upl_9", filename: "brief.pdf" }] };

describe("the chat panel's account of tool calls", () => {
  it("names a block by its words, not its id", () => {
    expect(describeCall("propose_edit", { block_id: "blk_1", content: {} }, look)).toBe("heading “Executive summary”");
    expect(describeCall("set_block_layout", { block_id: "blk_2", variant: "lead" }, look))
      .toBe("text “A phased electrification of about 80 vans a…”");
    expect(describeCall("set_block_layout", { block_id: "blk_3" }, look)).toBe("a chart block");
    expect(describeCall("propose_delete", { block_id: "blk_gone" }, look)).toBe("a block that is no longer there");
    expect(blockName({ type: "image", content: { caption: "Fleet by depot" } })).toBe("image “Fleet by depot”");
  });

  it("describes the other calls in words too", () => {
    expect(describeCall("read_blocks", { block_ids: ["blk_1", "blk_2"] }, look)).toBe("2 blocks");
    expect(describeCall("insert_blocks", { blocks: [{}, {}, {}] }, look)).toBe("3 blocks");
    expect(describeCall("read_upload", { upload_id: "upl_9" }, look)).toBe("brief.pdf");
    expect(describeCall("web_search", { query: "cool roof albedo" }, look)).toBe("“cool roof albedo”");
    expect(describeCall("fetch_url", { url: "https://example.org/a/b?c=1" }, look)).toBe("example.org");
    expect(describeCall("view_pages", { pages: [1, 2] }, look)).toBe("pages 1, 2");
    expect(describeCall("get_document", {}, look)).toBeNull();
  });

  it("labels every tool in plain words, known or not", () => {
    expect(toolLabel("set_typography")).toBe("Changed fonts and colours");
    expect(toolLabel("my_custom_tool")).toBe("my custom tool");
  });

  it("folds a long run into one line", () => {
    const run = [
      ...Array.from({ length: 21 }, () => ({ name: "set_block_layout", status: "success" })),
      ...Array.from({ length: 6 }, () => ({ name: "view_pages", status: "success" })),
      ...Array.from({ length: 5 }, () => ({ name: "propose_edit", status: "success" })),
      { name: "get_document", status: "success" },
      { name: "web_search", status: "error" },
      { name: "web_search", status: "error" },
    ];
    expect(groupCalls(run)[0]).toEqual({ label: "Changed a block's layout", count: 21, failed: 0 });
    expect(summarizeCalls(run)).toBe(
      "35 steps: changed a block's layout ×21, looked at the pages ×6, suggested an edit ×5 and 2 more kinds · 2 failed");
    expect(summarizeCalls(run.slice(0, 1))).toBe("1 step: changed a block's layout");
  });
});
