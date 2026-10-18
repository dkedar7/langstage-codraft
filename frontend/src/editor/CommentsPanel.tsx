import { Bot, Check, MessageSquarePlus, RotateCcw, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../api";
import type { Comment } from "../types";
import { blockText } from "./Diff";
import { useEditorApi } from "./context";
import { agentName, authorLabel } from "../branding";

export function CommentsPanel() {
  const ed = useEditorApi();
  const { doc } = ed;
  const [body, setBody] = useState("");
  const [showResolved, setShowResolved] = useState(false);
  const target = ed.commentFilter ?? ed.selected;
  const targetBlock = doc.blocks.find((b) => b.id === target);

  const order = useMemo(() => Object.fromEntries(doc.blocks.map((b, i) => [b.id, i])), [doc.blocks]);
  const list = useMemo(() => {
    let cs = doc.comments.filter((c) => showResolved || c.status === "open");
    if (ed.commentFilter) cs = cs.filter((c) => c.block_id === ed.commentFilter);
    return [...cs].sort((a, b) => ((a.block_id ? order[a.block_id] ?? 1e9 : -1) - (b.block_id ? order[b.block_id] ?? 1e9 : -1))
      || a.created_at - b.created_at);
  }, [doc.comments, showResolved, ed.commentFilter, order]);
  const open = doc.comments.filter((c) => c.status === "open");

  const add = async (askNow: boolean) => {
    if (!body.trim()) return;
    const c = await ed.run(api.addComment(doc.id, { body, block_id: targetBlock?.id ?? null }));
    setBody("");
    if (c && askNow) ed.ask(`Address comment ${c.id}${c.block_id ? ` on block ${c.block_id}` : ""}: ${c.body}`);
  };

  return (
    <div className="flex flex-col h-full">
      <div className="panel-section">
        <div className="flex items-center justify-between mb-2">
          <div className="font-semibold text-[13px]">
            {targetBlock ? <>Comment on <span className="chip">{targetBlock.type}</span></> : "Comment on the document"}
          </div>
          {ed.commentFilter && (
            <button className="btn btn-ghost btn-icon" title="Show all comments" onClick={() => ed.setCommentFilter(null)}><X size={14} /></button>
          )}
        </div>
        {targetBlock && (
          <div className="text-xs text-[var(--color-text-muted)] mb-2 line-clamp-2">“{blockText(targetBlock.type, targetBlock.content).slice(0, 140)}”</div>
        )}
        <textarea className="textarea" rows={3} value={body} data-testid="comment-input"
          placeholder={`Tag ${agentName(ed.cfg.ui)}: e.g. “make this more concise” or “find a source for this claim”`}
          onChange={(e) => setBody(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) add(false); }} />
        <div className="flex gap-2 mt-2">
          <button className="btn btn-primary" data-testid="comment-add" disabled={!body.trim()} onClick={() => add(false)}>
            <MessageSquarePlus size={14} /> Queue for {agentName(ed.cfg.ui, false)}</button>
          <button className="btn btn-agent" disabled={!body.trim() || !ed.cfg.chat_available} onClick={() => add(true)}>
            <Bot size={14} /> Ask now</button>
        </div>
      </div>
      <div className="panel-section flex items-center gap-2">
        <button className="btn btn-agent" data-testid="address-all" disabled={!open.length || ed.agentRunning || !ed.cfg.chat_available}
          onClick={() => ed.ask("Address all comments")}>
          <Bot size={14} /> Address all ({open.length}), top to bottom</button>
        <label className="ml-auto text-xs text-[var(--color-text-muted)] flex items-center gap-1">
          <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} /> resolved
        </label>
      </div>
      <div className="flex-1 overflow-auto scroll-thin">
        {!list.length && <div className="p-4 text-[var(--color-text-muted)] text-xs">No comments{ed.commentFilter ? " on this block" : ""}.</div>}
        {list.map((c) => <CommentCard key={c.id} c={c} />)}
      </div>
    </div>
  );
}

function CommentCard({ c }: { c: Comment }) {
  const ed = useEditorApi();
  const [reply, setReply] = useState("");
  const block = ed.doc.blocks.find((b) => b.id === c.block_id);
  return (
    <div className={`panel-section ${c.status === "resolved" ? "opacity-60" : ""}`} data-testid="comment-card">
      <div className="flex items-center gap-2 mb-1">
        <span className={`chip ${c.author === "agent" ? "chip-agent" : ""}`}>{authorLabel(ed.cfg.ui, c.author)}</span>
        {block ? (
          <button className="text-xs text-[var(--ui-accent)] truncate max-w-[180px]" onClick={() => ed.select(block.id)}>
            {block.type}: {blockText(block.type, block.content).slice(0, 40) || "(empty)"}
          </button>
        ) : <span className="text-xs text-[var(--color-text-muted)]">{c.block_id ? "deleted block" : "document"}</span>}
        <span className="ml-auto text-[11px] text-[var(--color-text-muted)]">{new Date(c.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
      </div>
      <div className="whitespace-pre-wrap">{c.body}</div>
      {c.replies.map((r, i) => (
        <div key={i} className={`mt-2 ml-3 pl-2 border-l-2 ${r.author === "agent" ? "border-[var(--color-primary)]" : "border-[var(--color-border)]"}`}>
          <span className={`text-[11px] font-semibold ${r.author === "agent" ? "text-[var(--color-primary)]" : "text-[var(--color-text-secondary)]"}`}>{authorLabel(ed.cfg.ui, r.author)}</span>
          <div className="whitespace-pre-wrap">{r.body}</div>
        </div>
      ))}
      <div className="flex gap-1 mt-2">
        {c.status === "open" ? (
          <>
            <input className="input" style={{ padding: "3px 6px" }} placeholder="Reply…" value={reply}
              onChange={(e) => setReply(e.target.value)}
              onKeyDown={async (e) => {
                if (e.key === "Enter" && reply.trim()) {
                  await ed.run(api.replyComment(c.id, reply));
                  setReply("");
                }
              }} />
            <button className="btn btn-icon" title={`Ask ${agentName(ed.cfg.ui)} about this comment`} disabled={!ed.cfg.chat_available || ed.agentRunning}
              onClick={() => ed.ask(`Address comment ${c.id}${c.block_id ? ` on block ${c.block_id}` : ""}: ${c.body}`)}><Bot size={14} /></button>
            <button className="btn btn-icon" title="Resolve" data-testid="comment-resolve"
              onClick={() => ed.run(api.setCommentStatus(c.id, "resolved"))}><Check size={14} /></button>
          </>
        ) : (
          <button className="btn btn-icon" title="Reopen" onClick={() => ed.run(api.setCommentStatus(c.id, "open"))}><RotateCcw size={14} /></button>
        )}
        <button className="btn btn-icon btn-danger" title="Delete" onClick={() => ed.run(api.deleteComment(c.id))}><Trash2 size={14} /></button>
      </div>
    </div>
  );
}
