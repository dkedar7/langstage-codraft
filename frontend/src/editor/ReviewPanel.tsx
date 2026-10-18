import { CheckCheck, Sparkles, XCircle } from "lucide-react";
import { api } from "../api";
import { blockText, SuggestionDiff } from "./Diff";
import { useEditorApi } from "./context";

export function ReviewPanel() {
  const ed = useEditorApi();
  const { doc } = ed;
  const order = Object.fromEntries(doc.blocks.map((b, i) => [b.id, i]));
  const pending = doc.suggestions
    .filter((s) => s.status === "pending")
    .sort((a, b) => ((a.block_id ? order[a.block_id] : order[a.payload.after] ?? 1e9) - (b.block_id ? order[b.block_id] : order[b.payload.after] ?? 1e9))
      || a.created_at - b.created_at);
  const auto = doc.settings.approval === "auto";

  return (
    <div className="flex flex-col h-full">
      <div className="panel-section">
        <div className="flex items-center gap-2">
          <div className="font-semibold">Agent suggestions</div>
          <span className="chip">{pending.length} pending</span>
        </div>
        <label className="flex items-center gap-2 mt-3 text-[12.5px]">
          <input type="checkbox" data-testid="auto-apply" checked={auto}
            onChange={(e) => ed.run(api.patchDoc(doc.id, { settings: { approval: e.target.checked ? "auto" : "required" } }))} />
          Apply agent edits without asking (still versioned, so you can step back)
        </label>
        {pending.length > 1 && (
          <div className="flex gap-2 mt-3">
            <button className="btn btn-agent" onClick={() => ed.run(api.bulk(doc.id, "accept-all"), "Accepted all")}>
              <CheckCheck size={14} /> Accept all</button>
            <button className="btn" onClick={() => ed.run(api.bulk(doc.id, "reject-all"), "Rejected all")}>
              <XCircle size={14} /> Reject all</button>
          </div>
        )}
      </div>
      <div className="flex-1 overflow-auto scroll-thin">
        {!pending.length && (
          <div className="p-4 text-[var(--color-text-muted)] text-xs">Nothing to review. When the agent proposes a change it shows up here and on the block.</div>
        )}
        {pending.map((s) => {
          const block = doc.blocks.find((b) => b.id === s.block_id);
          const comment = doc.comments.find((c) => c.id === s.comment_id);
          return (
            <div key={s.id} className="panel-section" data-testid="review-card">
              <div className="flex items-center gap-2 mb-1">
                <span className="chip chip-agent"><Sparkles size={10} /> {s.kind}</span>
                {block && (
                  <button className="text-xs text-[var(--ui-accent)] truncate" onClick={() => ed.select(block.id)}>
                    {block.type}: {blockText(block.type, block.content).slice(0, 36)}
                  </button>
                )}
              </div>
              {s.note && <div className="text-[var(--color-text-secondary)] text-xs mb-2">{s.note}</div>}
              {comment && <div className="text-xs text-[var(--color-warning)] bg-[color-mix(in_srgb,var(--color-warning)_12%,transparent)] rounded px-2 py-1 mb-2">Re: “{comment.body}”</div>}
              <div className="bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded p-2 max-h-56 overflow-auto scroll-thin">
                {s.kind === "edit" ? <SuggestionDiff block={block} content={s.payload.content || {}} />
                  : s.kind === "insert" ? <div className="whitespace-pre-wrap text-[12.5px]"><span className="diff-add">{blockText(s.payload.type, s.payload.content || {})}</span></div>
                  : s.kind === "delete" ? <span className="diff-del">{block ? blockText(block.type, block.content) : "block"}</span>
                  : <span>Move to {s.payload.after ? "after another block" : "the start"}.</span>}
              </div>
              {s.payload.refs?.length ? (
                <div className="text-[11.5px] text-[var(--color-text-muted)] mt-1">Sources: {s.payload.refs.map((r: any) => r.title).join("; ")}</div>
              ) : null}
              <div className="flex gap-2 mt-2">
                <button className="btn btn-agent" data-testid="review-accept" onClick={() => ed.run(api.accept(s.id), "Accepted")}>Accept</button>
                <button className="btn" data-testid="review-reject" onClick={() => ed.run(api.reject(s.id), "Rejected")}>Reject</button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
