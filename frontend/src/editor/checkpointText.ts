import type { Checkpoint, CheckpointRestore } from "../api";
import { agentName } from "../branding";
import type { UiConfig } from "../types";

/** The words the checkpoint views use (gh #61): names, counts and what a restore did. */

export function ago(t: number, now = Date.now() / 1000): string {
  const s = Math.max(0, now - t);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86400);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

/** What a checkpoint is called: its name, or for an automatic one what it was taken before. */
export function checkpointName(ui: UiConfig | undefined, c: Checkpoint): string {
  return c.kind === "auto" && c.reason === "turn" ? `Before ${agentName(ui)}'s turn` : c.name;
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "3 edited, 1 added" for a checkpoint's change counts. */
export function changeSummary(n: { edited: number; added: number; deleted: number; moved: number }): string {
  return [n.edited ? `${n.edited} edited` : "", n.added ? `${n.added} added` : "", n.deleted ? `${n.deleted} deleted` : "",
    n.moved ? `${n.moved} moved` : ""].filter(Boolean).join(", ");
}

/** One line for the toast after a restore. */
export function restoreSummary(r: CheckpointRestore): string {
  const parts = [
    r.edited ? `${plural(r.edited, "block")} back to the earlier text` : "",
    r.restored ? `${plural(r.restored, "deleted block")} brought back` : "",
    r.removed ? `${plural(r.removed, "added block")} moved to Recently deleted` : "",
    r.moved ? `${plural(r.moved, "block")} moved back` : "",
  ].filter(Boolean);
  const skipped = r.skipped.length
    ? ` ${plural(r.skipped.length, "block")} left alone (${r.skipped[0].reason}).` : "";
  return `${parts.join(", ") || "Nothing changed"}.${skipped}${r.notes.length ? ` ${r.notes.join("; ")}.` : ""}`;
}
