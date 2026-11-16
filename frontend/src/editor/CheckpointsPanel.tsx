import { ArrowLeft, Bookmark, Check, History, Pencil, RotateCcw, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type Checkpoint, type CheckpointChanges, type CheckpointRestore } from "../api";
import { AgentName } from "../branding";
import { Boundary } from "../render/Boundary";
import { ago, changeSummary, checkpointName, plural, restoreSummary } from "./checkpointText";
import { blockText, Diff } from "./Diff";
import { useEditorApi } from "./context";

export { changeSummary } from "./checkpointText";

/** Restore a checkpoint (all of it, or some blocks) and say what happened, with an Undo. */
export function useCheckpointRestore() {
  const ed = useEditorApi();
  const restore = async (cid: string, blocks?: string[], lead = "Restored"): Promise<CheckpointRestore | undefined> => {
    const r = await ed.run(api.restoreCheckpoint(ed.doc.id, cid, ed.me.id, blocks));
    if (!r) return undefined;
    const res = r.result;
    if (!res.changed) {
      ed.toast(res.skipped.length ? `Nothing restored: ${restoreSummary(res)}` : "Nothing to restore: the text is already as it was then.", "info");
      return res;
    }
    ed.toast(`${lead}: ${restoreSummary(res)} Nothing is lost: newer text stays in each block's history.`, "info",
      res.undo ? { label: "Undo", run: () => { restore(res.undo!, undefined, "Undone"); } } : undefined);
    return res;
  };
  return restore;
}

/** Document → Checkpoints: name the text as it is now, see what changed since any
 *  checkpoint, and go back to one. Automatic ones are taken before an agent turn, an
 *  Accept all and a restore. */
export function Checkpoints() {
  const ed = useEditorApi();
  const { doc, cfg } = ed;
  const [items, setItems] = useState<Checkpoint[] | null>(null);
  const [all, setAll] = useState(false);
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  useEffect(() => {
    let alive = true;
    api.checkpoints(doc.id).then((r) => { if (alive) setItems(r.checkpoints); }).catch(() => { if (alive) setItems([]); });
    return () => { alive = false; };
  }, [doc.id, doc.rev]);
  const save = async () => {
    const n = name.trim();
    if (!n) return;
    const c = await ed.run(api.createCheckpoint(doc.id, n), `Checkpoint “${n}” saved.`);
    if (c) setName("");
  };
  const who = (c: Checkpoint) => (c.by === "agent" ? AgentName(cfg.ui)
    : !c.by || ["user", "author", "human"].includes(c.by.toLowerCase()) ? "You" : c.by);
  // Your own checkpoints are always listed; of the automatic ones, the three newest until asked.
  const recent = new Set((items || []).filter((c) => c.kind === "auto").slice(0, 3).map((c) => c.id));
  const shown = (items || []).filter((c) => all || c.kind === "named" || recent.has(c.id));
  const hidden = (items || []).length - shown.length;
  return (
    <div className="panel-section" data-testid="checkpoints">
      <div className="font-semibold">Checkpoints</div>
      <div className="text-[11.5px] text-[var(--color-text-muted)] mt-0.5 mb-2">
        A checkpoint remembers the text as it is now. Later you can see what changed since, and go back: all of it or
        block by block. One is also taken before each agent turn and each Accept all.
      </div>
      <div className="flex gap-1.5">
        <input className="input flex-1" placeholder="e.g. Sent to legal" value={name} maxLength={80} data-testid="checkpoint-name"
          onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") save(); }} />
        <button className="btn" data-testid="checkpoint-save" disabled={!name.trim()} onClick={save}>
          <Bookmark size={13} /> Save</button>
      </div>
      {items && items.length === 0 && (
        <div className="text-[12px] text-[var(--color-text-muted)] mt-2" data-testid="checkpoints-empty">No checkpoints yet.</div>)}
      <div className="mt-2 space-y-1.5" data-testid="checkpoint-list">
        {shown.map((c) => {
          const n = c.changes?.total ?? 0;
          return (
            <div key={c.id} data-testid="checkpoint" data-checkpoint-id={c.id} data-kind={c.kind} data-reason={c.reason}
              className="rounded border border-[var(--color-border)] px-2 py-1.5 text-[12px]">
              {renaming?.id === c.id ? (
                <div className="flex gap-1">
                  <input className="input flex-1" autoFocus value={renaming.name} maxLength={80} data-testid="checkpoint-rename-input"
                    onChange={(e) => setRenaming({ id: c.id, name: e.target.value })}
                    onKeyDown={async (e) => {
                      if (e.key === "Escape") setRenaming(null);
                      if (e.key === "Enter" && renaming.name.trim()) {
                        await ed.run(api.renameCheckpoint(doc.id, c.id, renaming.name.trim()));
                        setRenaming(null);
                      }
                    }} />
                  <button className="btn btn-icon" title="Save the name" data-testid="checkpoint-rename-save" disabled={!renaming.name.trim()}
                    onClick={async () => { await ed.run(api.renameCheckpoint(doc.id, c.id, renaming.name.trim())); setRenaming(null); }}>
                    <Check size={13} /></button>
                  <button className="btn btn-icon btn-ghost" title="Cancel" onClick={() => setRenaming(null)}><X size={13} /></button>
                </div>
              ) : (
                <div className="flex items-center gap-1.5">
                  {c.kind === "named" ? <Bookmark size={11} className="text-[var(--ui-accent)] flex-none" />
                    : <History size={11} className="text-[var(--color-text-muted)] flex-none" />}
                  <span className="font-semibold truncate" title={checkpointName(cfg.ui, c)} data-testid="checkpoint-title">{checkpointName(cfg.ui, c)}</span>
                  <span className="ml-auto flex gap-0.5 flex-none">
                    <button className="btn btn-ghost btn-icon" data-testid="checkpoint-rename"
                      title={c.kind === "auto" ? "Name this checkpoint to keep it" : "Rename"}
                      onClick={() => setRenaming({ id: c.id, name: c.kind === "named" ? c.name : "" })}><Pencil size={12} /></button>
                    <button className="btn btn-ghost btn-icon btn-danger" data-testid="checkpoint-delete"
                      title="Forget this checkpoint (no text is deleted)"
                      onClick={() => { if (c.kind === "auto" || confirm(`Forget the checkpoint “${c.name}”? No text is deleted.`)) ed.run(api.deleteCheckpoint(doc.id, c.id)); }}>
                      <Trash2 size={12} /></button>
                  </span>
                </div>
              )}
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className="text-[var(--color-text-muted)] truncate">{who(c)} · {ago(c.at)}</span>
                <span className="ml-auto flex-none">
                  {n > 0 ? (
                    <button className="btn btn-ghost text-[11.5px]" data-testid="checkpoint-changes" title={changeSummary(c.changes!)}
                      onClick={() => ed.setCheckpointReview(c.id)}>{plural(n, "change")} since…</button>
                  ) : <span className="text-[11.5px] text-[var(--color-text-muted)]" data-testid="checkpoint-unchanged">no changes since</span>}
                </span>
              </div>
            </div>
          );
        })}
      </div>
      {items && items.filter((c) => c.kind === "auto").length > 3 && (
        <button className="btn btn-ghost text-[11.5px] mt-1" data-testid="checkpoints-more" onClick={() => setAll(!all)}>
          {all ? "Show fewer automatic ones" : `Show ${hidden} older automatic one${hidden === 1 ? "" : "s"}`}</button>)}
    </div>
  );
}

type Card = { id: string; type: string; kinds: string[]; body: React.ReactNode; action: string; live: boolean };

/** Changes since a checkpoint: one card per changed block, with the same word diff a
 *  suggestion gets. A block can be reverted on its own, or everything restored at once. */
export function CheckpointReview({ id }: { id: string }) {
  const ed = useEditorApi();
  const { doc, cfg } = ed;
  const [ch, setCh] = useState<CheckpointChanges | null>(null);
  const [gone, setGone] = useState(false);
  const restore = useCheckpointRestore();
  useEffect(() => {
    let alive = true;
    api.checkpointChanges(doc.id, id).then((r) => { if (alive) setCh(r); }).catch(() => { if (alive) setGone(true); });
    return () => { alive = false; };
  }, [doc.id, doc.rev, id]);
  const back = () => ed.setCheckpointReview(null);
  if (gone) {
    return (
      <div className="panel-section">
        <button className="btn btn-ghost" onClick={back}><ArrowLeft size={13} /> Checkpoints</button>
        <div className="text-xs text-[var(--color-text-muted)] mt-2">This checkpoint no longer exists.</div>
      </div>
    );
  }
  if (!ch) return <div className="p-4 text-xs text-[var(--color-text-muted)]">Loading…</div>;

  const cards = new Map<string, Card>();
  const card = (bid: string, type: string, kind: string, body: React.ReactNode, action: string, live: boolean) => {
    const c = cards.get(bid);
    if (c) { c.kinds.push(kind); c.body = <>{c.body}{body}</>; c.action = "Revert this block"; } else cards.set(bid, { id: bid, type, kinds: [kind], body, action, live });
  };
  for (const e of ch.edited) {
    const before = blockText(e.type, e.before?.content || {});
    const after = blockText(e.type, e.after?.content || {});
    card(e.block_id, e.type, "edited", (
      <>
        {before !== after ? <Diff before={before} after={after} />
          : <div className="text-xs text-[var(--color-text-secondary)]">{e.refs_changed ? "Its sources changed." : "Changed in a way that has no text to compare."}</div>}
        {before !== after && e.refs_changed && <div className="text-[11.5px] text-[var(--color-text-muted)] mt-1">Its sources changed too.</div>}
        <div className="text-[11px] text-[var(--color-text-muted)] mt-1">{e.words_before} → {e.words_after} words</div>
      </>), "Revert this block", true);
  }
  for (const b of ch.added) {
    card(b.block_id, b.type, "added",
      <div className="whitespace-pre-wrap text-[12.5px]"><span className="diff-add">{blockText(b.type, b.content || {}) || `(${b.type})`}</span></div>,
      "Remove this block", true);
  }
  for (const b of ch.deleted) {
    card(b.block_id, b.type, "deleted",
      <div className="whitespace-pre-wrap text-[12.5px]"><span className="diff-del">{blockText(b.type, b.content || {}) || `(${b.type})`}</span></div>,
      "Bring it back", false);
  }
  for (const b of ch.moved) {
    card(b.block_id, b.type, "moved",
      <div className="text-[12px] text-[var(--color-text-secondary)] mt-1" data-testid="checkpoint-moved">
        Moved {b.now < b.was ? "up" : "down"} (it was block {b.was}, now {b.now}){b.preview ? <>: <span className="text-[var(--color-text-muted)]">{b.preview.slice(0, 80)}</span></> : null}
      </div>, "Move it back", true);
  }
  const order = Object.fromEntries(doc.blocks.map((b, i) => [b.id, i]));
  const list = [...cards.values()].sort((a, b) => (order[a.id] ?? 1e9) - (order[b.id] ?? 1e9));
  const c = ch.checkpoint;

  return (
    <div className="flex flex-col h-full" data-testid="checkpoint-review">
      <div className="panel-section">
        <button className="btn btn-ghost -ml-1.5" data-testid="checkpoint-back" onClick={back}><ArrowLeft size={13} /> Checkpoints</button>
        <div className="flex items-center gap-2 mt-1">
          <div className="font-semibold truncate" data-testid="checkpoint-review-title">Changes since “{checkpointName(cfg.ui, c)}”</div>
          <span className="chip flex-none">{ago(c.at)}</span>
        </div>
        <div className="text-[12px] text-[var(--color-text-secondary)] mt-1" data-testid="checkpoint-review-summary">
          {ch.counts.total ? `${changeSummary(ch.counts)}. ${ch.words_before.toLocaleString()} → ${ch.words_after.toLocaleString()} words.`
            : "Nothing changed: the text is exactly as it was then."}
        </div>
        <div className="text-[11.5px] text-[var(--color-text-muted)] mt-1">
          Text only; the look has its own history under Design. Going back overwrites nothing: newer text stays in each block's history.
        </div>
        {ch.counts.total > 0 && (
          <button className="btn btn-primary mt-2" data-testid="checkpoint-restore-all"
            onClick={() => restore(c.id, undefined, "Restored")}>
            <RotateCcw size={13} /> Restore all {ch.counts.total > 1 ? ch.counts.total : ""}</button>
        )}
      </div>
      <div className="flex-1 overflow-auto scroll-thin">
        {list.map((k) => {
          const block = doc.blocks.find((b) => b.id === k.id);
          return (
            <div key={k.id} className="panel-section" data-testid="checkpoint-card" data-block-id={k.id} data-kinds={k.kinds.join(" ")}>
              <div className="flex items-center gap-2 mb-1">
                {k.kinds.map((kind) => <span key={kind} className="chip">{kind}</span>)}
                {block && k.live ? (
                  <button className="text-xs text-[var(--ui-accent)] truncate" title="Show this block on the page" onClick={() => ed.select(block.id)}>
                    {block.type}: {String(blockText(block.type, block.content)).slice(0, 36)}</button>
                ) : <span className="text-xs text-[var(--color-text-muted)]">{k.type}</span>}
              </div>
              <div className="bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded p-2 max-h-56 overflow-auto scroll-thin">
                <Boundary fallback={<span className="text-xs">This change can't be previewed.</span>}>{k.body}</Boundary>
              </div>
              <div className="flex gap-2 mt-2">
                <button className="btn" data-testid="checkpoint-revert" onClick={() => restore(c.id, [k.id], "Reverted")}>
                  <RotateCcw size={13} /> {k.action}</button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
