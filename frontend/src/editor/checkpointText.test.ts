import { describe, expect, it } from "vitest";
import type { Checkpoint, CheckpointRestore } from "../api";
import { ago, changeSummary, checkpointName, restoreSummary } from "./checkpointText";

const cp = (over: Partial<Checkpoint>): Checkpoint => ({
  id: "chk_1", name: "Sent to legal", kind: "named", reason: "", detail: "", by: "Dana", at: 0, created_at: 0, blocks: 3, ...over,
});
const done = (over: Partial<CheckpointRestore>): CheckpointRestore => ({
  changed: true, edited: 0, restored: 0, removed: 0, moved: 0, skipped: [], undo: "chk_2", notes: [], ...over,
});

describe("the words of the checkpoint views", () => {
  it("calls a checkpoint by its name, and the agent's by the agent's name", () => {
    expect(checkpointName(undefined, cp({}))).toBe("Sent to legal");
    expect(checkpointName(undefined, cp({ kind: "auto", reason: "turn", name: "Before the agent's turn" }))).toBe("Before the agent's turn");
    expect(checkpointName({ agent_name: "Juniper" } as any, cp({ kind: "auto", reason: "turn", name: "Before the agent's turn" })))
      .toBe("Before Juniper's turn");
    expect(checkpointName({ agent_name: "Juniper" } as any, cp({ kind: "auto", reason: "accept-all", name: "Before Accept all (5 suggestions)" })))
      .toBe("Before Accept all (5 suggestions)");
    // An automatic checkpoint the author named keeps the name they gave it.
    expect(checkpointName({ agent_name: "Juniper" } as any, cp({ kind: "named", reason: "turn", name: "Before the rewrite" })))
      .toBe("Before the rewrite");
  });

  it("counts only what changed", () => {
    expect(changeSummary({ edited: 3, added: 1, deleted: 0, moved: 2 })).toBe("3 edited, 1 added, 2 moved");
    expect(changeSummary({ edited: 0, added: 0, deleted: 1, moved: 0 })).toBe("1 deleted");
    expect(changeSummary({ edited: 0, added: 0, deleted: 0, moved: 0 })).toBe("");
  });

  it("says what a restore did, what it left alone and why", () => {
    expect(restoreSummary(done({ edited: 2, restored: 1, removed: 1, moved: 1 })))
      .toBe("2 blocks back to the earlier text, 1 deleted block brought back, 1 added block moved to Recently deleted, 1 block moved back.");
    expect(restoreSummary(done({ edited: 1, skipped: [{ block_id: "blk_1", reason: "Sam is editing it" }] })))
      .toBe("1 block back to the earlier text. 1 block left alone (Sam is editing it).");
    expect(restoreSummary(done({ changed: false, notes: ["1 block(s) of the checkpoint no longer exist"] })))
      .toBe("Nothing changed. 1 block(s) of the checkpoint no longer exist.");
  });

  it("tells the time the way the design history does", () => {
    expect(ago(1000, 1010)).toBe("just now");
    expect(ago(1000, 1000 + 180)).toBe("3 min ago");
    expect(ago(1000, 1000 + 7200)).toBe("2 h ago");
    expect(ago(1000, 1000 + 86400)).toBe("1 day ago");
    expect(ago(1000, 1000 + 6 * 86400)).toBe("6 days ago");
  });
});
