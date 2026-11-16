import { CheckCheck, Sparkles, Undo2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type Checkpoint } from "../api";
import { changeSummary, useCheckpointRestore } from "./CheckpointsPanel";
import { blockText, GoneNote, OutdatedNote, SuggestionDiff } from "./Diff";
import { useEditorApi } from "./context";
import { agentName, agentNamed } from "../branding";
import { Boundary } from "../render/Boundary";

export function ReviewPanel() {
  const ed = useEditorApi();
  const { doc } = ed;
  const order = Object.fromEntries(doc.blocks.map((b, i) => [b.id, i]));
  const pending = doc.suggestions
    .filter((s) => s.status === "pending")
    .sort((a, b) => ((a.block_id ? order[a.block_id] : order[a.payload.after] ?? 1e9) - (b.block_id ? order[b.block_id] : order[b.payload.after] ?? 1e9))
      || a.created_at - b.created_at);
  const auto = doc.settings.approval === "auto";
  // The newest Accept all that still shows in the text can be undone as a whole (gh #61).
  const restore = useCheckpointRestore();
  const [lastAll, setLastAll] = useState<Checkpoint | null>(null);
  useEffect(() => {
    let alive = true;
    api.checkpoints(doc.id).then((r) => {
      if (!alive) return;
      // Restores come and go on top of it; a newer agent turn or Accept all replaces it.
      const newest = r.checkpoints.find((c) => c.kind === "auto" && (c.reason === "accept-all" || c.reason === "turn"));
      setLastAll(newest && newest.reason === "accept-all" && (newest.changes?.total ?? 0) > 0 ? newest : null);
    }).catch(() => {});
    return () => { alive = false; };
  }, [doc.id, doc.rev]);
  const undoAll = (cid: string) => restore(cid, undefined, "Accept all undone");

  return (
    <div className="flex flex-col h-full">
      <div className="panel-section">
        <div className="flex items-center gap-2">
          <div className="font-semibold">{agentNamed(ed.cfg.ui) ? `Suggestions from ${ed.cfg.ui!.agent_name}` : "Agent suggestions"}</div>
          <span className="chip">{pending.length} pending</span>
        </div>
        <label className="flex items-center gap-2 mt-3 text-[12.5px]">
          <input type="checkbox" data-testid="auto-apply" checked={auto}
            onChange={(e) => ed.run(api.patchDoc(doc.id, { settings: { approval: e.target.checked ? "auto" : "required" } }))} />
          Apply {agentNamed(ed.cfg.ui) ? `${ed.cfg.ui!.agent_name}'s` : "agent"} edits without asking (still versioned, so you can step back)
        </label>
        {pending.length > 1 && (
          <div className="flex gap-2 mt-3">
            <button className="btn btn-agent" data-testid="accept-all" onClick={async () => {
              const r = await ed.run(api.bulk(doc.id, "accept-all"));
              if (!r) return;
              const gone = r.skipped_gone?.length ?? 0;
              const failed = r.failed ?? [];
              const stale = (r.skipped?.length ?? 0) - gone - failed.length;
              const parts = [
                stale ? `${stale} outdated suggestion${stale > 1 ? "s" : ""} (the block changed since)` : "",
                gone ? `${gone} on deleted block${gone > 1 ? "s" : ""}` : "",
                failed.length ? `${failed.length} that could not be applied (${failed[0].reason})` : "",
              ].filter(Boolean);
              ed.toast(parts.length ? `Accepted ${r.count}; skipped ${parts.join(" and ")}` : `Accepted all ${r.count}.`, "info",
                r.checkpoint ? { label: "Undo accept all", run: () => { undoAll(r.checkpoint!); } } : undefined);
            }}>
              <CheckCheck size={14} /> Accept all</button>
            <button className="btn" onClick={() => ed.run(api.bulk(doc.id, "reject-all"), "Rejected all")}>
              <XCircle size={14} /> Reject all</button>
          </div>
        )}
      </div>
      {lastAll && (
        <div className="panel-section flex items-center gap-2 text-[12px]" data-testid="last-accept-all">
          <span className="text-[var(--color-text-secondary)] min-w-0 flex-1">
            Since your last Accept all: {changeSummary(lastAll.changes!)}.</span>
          <button className="btn btn-ghost text-[11.5px] flex-none" data-testid="review-accept-all-changes"
            onClick={() => ed.setCheckpointReview(lastAll.id)}>See changes</button>
          <button className="btn flex-none" data-testid="undo-accept-all" title="Put the text back as it was before that Accept all. Nothing is lost: the accepted text stays in each block's history."
            onClick={() => undoAll(lastAll.id)}><Undo2 size={13} /> Undo</button>
        </div>
      )}
      <div className="flex-1 overflow-auto scroll-thin">
        {!pending.length && (
          <div className="p-4 text-[var(--color-text-muted)] text-xs">Nothing to review. When {agentName(ed.cfg.ui)} proposes a change it shows up here and on the block.</div>
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
                    {block.type}: {String(blockText(block.type, block.content)).slice(0, 36)}
                  </button>
                )}
              </div>
              {s.note && <div className="text-[var(--color-text-secondary)] text-xs mb-2">{s.note}</div>}
              {s.gone ? <GoneNote why={s.gone} /> : s.outdated && <OutdatedNote />}
              {comment && <div className="text-xs text-[var(--color-warning)] bg-[color-mix(in_srgb,var(--color-warning)_12%,transparent)] rounded px-2 py-1 mb-2">Re: “{comment.body}”</div>}
              <div className="bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded p-2 max-h-56 overflow-auto scroll-thin">
                <Boundary fallback={<span className="text-xs">This suggestion can't be previewed.</span>}>
                {s.kind === "edit" ? <SuggestionDiff block={block} content={s.payload.content || {}} base={s.base_content} />
                  : s.kind === "insert" ? <div className="whitespace-pre-wrap text-[12.5px]"><span className="diff-add">{blockText(s.payload.type, s.payload.content || {})}</span></div>
                  : s.kind === "delete" ? <span className="diff-del">{block ? blockText(block.type, block.content) : "block"}</span>
                  : <span>Move to {s.payload.after ? "after another block" : "the start"}.</span>}
                </Boundary>
              </div>
              {s.payload.refs?.length ? (
                <div className="text-[11.5px] text-[var(--color-text-muted)] mt-1">Sources: {s.payload.refs.map((r: any) => r.title).join("; ")}</div>
              ) : null}
              <div className="flex gap-2 mt-2">
                {!s.gone && <button className="btn btn-agent" data-testid="review-accept"
                  onClick={() => ed.run(api.accept(s.id, undefined, !!s.outdated), s.outdated ? "Accepted as a new branch" : "Accepted")}>
                  {s.outdated ? "Accept anyway" : "Accept"}</button>}
                <button className="btn" data-testid="review-reject"
                  onClick={() => ed.run(api.reject(s.id), s.gone ? "Dismissed" : "Rejected")}>{s.gone ? "Dismiss" : "Reject"}</button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
