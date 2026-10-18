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

export function SuggestionDiff({ block, content }: { block: Block | undefined; content: Record<string, any> }) {
  if (!block) return <div className="text-xs text-[var(--color-text-muted)]">(block no longer exists)</div>;
  const merged = { ...block.content, ...content };
  return <Diff before={blockText(block.type, block.content)} after={blockText(block.type, merged)} />;
}
