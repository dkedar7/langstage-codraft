import { diffWords } from "diff";
import type { Block } from "../types";

export function blockText(type: string, content: Record<string, any>): string {
  if (type === "text" || type === "callout") return content.markdown || "";
  if (type === "heading") return content.text || "";
  if (type === "table") return (content.rows || []).map((r: string[]) => r.join(" | ")).join("\n");
  if (type === "image") return [content.src, content.caption].filter(Boolean).join("\n");
  if (type === "chart") return JSON.stringify(content.spec || {}, null, 1);
  return JSON.stringify(content);
}

/** Word-level diff of a block's current content and a suggested replacement. */
export function Diff({ before, after }: { before: string; after: string }) {
  const parts = diffWords(before, after);
  return (
    <div style={{ whiteSpace: "pre-wrap", fontSize: 12.5, lineHeight: 1.5 }}>
      {parts.map((p, i) => (
        <span key={i} className={p.added ? "diff-add" : p.removed ? "diff-del" : undefined}>{p.value}</span>
      ))}
    </div>
  );
}

export function SuggestionDiff({ block, content, base }: {
  block: Block | undefined; content: Record<string, any>; base?: Record<string, any>;
}) {
  if (!block) return <div className="text-xs text-[var(--color-text-muted)]">(block no longer exists)</div>;
  // An outdated suggestion is diffed against the text the agent saw, not the newer text (gh #37).
  const before = base ?? block.content;
  const merged = { ...before, ...content };
  return <Diff before={blockText(block.type, before)} after={blockText(block.type, merged)} />;
}

/** Shown on a suggestion that can no longer be applied because its block was deleted (gh #39). */
export function GoneNote({ why }: { why: string }) {
  return (
    <div className="text-xs text-[var(--color-text-secondary)] bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded px-2 py-1 mb-2"
      data-testid="suggestion-gone">
      Can't be applied: {why.replace(/^block \S+ /, "the block ")}. Dismiss it to clear it from the list.
    </div>
  );
}

/** Shown on a suggestion whose block was edited after it was proposed (gh #37). */
export function OutdatedNote() {
  return (
    <div className="text-xs text-[var(--color-warning)] bg-[color-mix(in_srgb,var(--color-warning)_12%,transparent)] rounded px-2 py-1 mb-2"
      data-testid="suggestion-outdated">
      Outdated: this block was edited after the suggestion was made. The diff shows the change against the
      text the agent saw. Accepting it anyway adds it as a new branch; the newer version stays in the block's history.
    </div>
  );
}
