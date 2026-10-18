import {
  ArrowUp, CheckCircle2, ChevronRight, Eye, FileSearch, GitPullRequestArrow, Globe, ImagePlus, Loader2,
  MessageSquareReply, Palette, Search, Sparkles, Square, User, Wrench, XCircle,
} from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { api, streamChat, type Frame } from "../api";
import { agentNamed } from "../branding";
import { mdToHtml } from "../render/markdown";
import { useEditorApi } from "./context";

interface ToolCall { id: string; name: string; args: any; status: "running" | "success" | "error"; result?: string; durationMs?: number }
interface Msg { id: string; role: "user" | "assistant" | "error"; content: string; tools: ToolCall[]; startedAt?: number; durationMs?: number }

let seq = 0;
const nid = () => `m${Date.now()}-${seq++}`;

function fromHistory(hist: any[]): Msg[] {
  const out: Msg[] = [];
  const results: Record<string, string> = {};
  for (const m of hist) if (m.role === "tool" && m.tool_call_id) results[m.tool_call_id] = m.content;
  for (const m of hist) {
    if (m.role === "user") out.push({ id: m.id || nid(), role: "user", content: m.content, tools: [] });
    else if (m.role === "assistant") {
      const last = out[out.length - 1];
      const tools: ToolCall[] = (m.tool_calls || []).map((t: any) => ({
        id: t.id, name: t.name, args: t.args, status: "success", result: results[t.id] }));
      if (last && last.role === "assistant") {
        last.content = [last.content, m.content].filter(Boolean).join("\n\n");
        last.tools.push(...tools);
      } else out.push({ id: m.id || nid(), role: "assistant", content: m.content || "", tools });
    }
  }
  return out;
}

const TOOL_META: Record<string, { label: string; icon: typeof Wrench }> = {
  get_document: { label: "Read the outline", icon: FileSearch },
  read_blocks: { label: "Read blocks", icon: FileSearch },
  search_document: { label: "Searched the document", icon: Search },
  read_upload: { label: "Read an upload", icon: FileSearch },
  list_comments: { label: "Listed comments", icon: MessageSquareReply },
  get_block_history: { label: "Checked history", icon: FileSearch },
  propose_edit: { label: "Suggested an edit", icon: GitPullRequestArrow },
  propose_insert: { label: "Suggested a new block", icon: GitPullRequestArrow },
  propose_delete: { label: "Suggested a deletion", icon: GitPullRequestArrow },
  propose_move: { label: "Suggested a move", icon: GitPullRequestArrow },
  reply_to_comment: { label: "Replied to a comment", icon: MessageSquareReply },
  set_block_layout: { label: "Changed block layout", icon: Palette },
  set_document_style: { label: "Changed document style", icon: Palette },
  compile_references: { label: "Compiled references", icon: FileSearch },
  view_pages: { label: "Looked at the pages", icon: Eye },
  view_sketch: { label: "Looked at the sketch", icon: Eye },
  generate_image: { label: "Generated an image", icon: ImagePlus },
  web_search: { label: "Searched the web", icon: Globe },
  fetch_url: { label: "Read a web page", icon: Globe },
};

function argPreview(args: Record<string, unknown>): string | null {
  const keys = Object.keys(args || {});
  if (!keys.length) return null;
  const v = args[keys[0]];
  if (v == null) return null;
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 60 ? s.slice(0, 60) + "…" : s;
}

/** langstage-web's ToolCallCard, with Codraft's plain-language labels. */
function ToolCallCard({ tc }: { tc: ToolCall }) {
  const [open, setOpen] = useState(false);
  const meta = TOOL_META[tc.name] || { label: tc.name, icon: Wrench };
  const Icon = meta.icon;
  const status = tc.status === "running" ? <Loader2 size={12} className="animate-spin text-[var(--color-text-muted)]" />
    : tc.status === "success" ? <CheckCircle2 size={12} className="text-[var(--color-success)]" />
      : <XCircle size={12} className="text-[var(--color-error)]" />;
  const prev = argPreview(tc.args);
  return (
    <div className="rounded border border-[var(--color-border)] bg-[var(--color-surface-2)] overflow-hidden" data-testid="tool-card">
      <button onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full px-3 py-1.5 text-left text-xs hover:bg-[var(--color-surface-3)] transition-colors">
        <ChevronRight size={12} className={`text-[var(--color-text-muted)] transition-transform ${open ? "rotate-90" : ""}`} />
        <Icon size={12} className="text-[var(--color-text-muted)]" />
        <span className="font-medium text-[var(--color-text-secondary)]">{meta.label}</span>
        {prev && <code className="text-[11px] text-[var(--color-text-muted)] truncate max-w-[160px]">({prev})</code>}
        <span className="flex-1" />
        {tc.durationMs != null && (
          <span className="text-[11px] tabular-nums text-[var(--color-text-muted)]">
            {tc.durationMs < 1000 ? `${Math.round(tc.durationMs)}ms` : `${(tc.durationMs / 1000).toFixed(1)}s`}</span>
        )}
        {status}
      </button>
      {open && (
        <div className="border-t border-[var(--color-border)] px-3 py-2 space-y-2">
          {Object.keys(tc.args || {}).length > 0 && (
            <div>
              <div className="text-[11px] font-medium uppercase tracking-wide text-[var(--color-text-muted)] mb-1">Arguments</div>
              <pre className="bg-[var(--color-surface-3)] rounded p-2 text-xs overflow-x-auto whitespace-pre-wrap break-all text-[var(--color-text)]">
                {JSON.stringify(tc.args, null, 2)}</pre>
            </div>
          )}
          {tc.result != null && (
            <div>
              <div className="text-[11px] font-medium uppercase tracking-wide text-[var(--color-text-muted)] mb-1">Result</div>
              <pre className="bg-[var(--color-surface-3)] rounded p-2 text-xs overflow-auto max-h-[300px] whitespace-pre-wrap break-all text-[var(--color-text)]">
                {tc.result}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Avatar({ role }: { role: "user" | "assistant" }) {
  return role === "user" ? (
    <div className="w-7 h-7 rounded-lg bg-[var(--color-surface-3)] flex items-center justify-center">
      <User size={14} className="text-[var(--color-text-secondary)]" /></div>
  ) : (
    <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-[var(--color-primary)] to-[var(--color-primary-dark)] flex items-center justify-center">
      <Sparkles size={14} className="text-white" /></div>
  );
}

function fmtDuration(ms: number) {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export interface ChatHandle { send: (message: string) => void }

export const ChatPanel = forwardRef<ChatHandle>(function ChatPanel(_, ref) {
  const ed = useEditorApi();
  const { doc, cfg } = ed;
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  // A message sent while the agent is still working waits here and goes when the turn ends
  // (pressing Enter used to do nothing at all mid-turn).
  const [queued, setQueued] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const branded = agentNamed(cfg.ui) ? cfg.ui!.agent_name! : "";
  const agentName = branded || (cfg.agent.kind === "demo" ? "Demo co-author" : "Co-author");

  // Load past turns. A message sent before they arrive (a new document's kickoff) waits for
  // them; otherwise the history would land on top of the running turn and hide it.
  const history = useRef<Promise<void>>(Promise.resolve());
  useEffect(() => {
    let alive = true;
    history.current = api.messages(doc.id).then((h) => { if (alive) setMsgs(fromHistory(h)); }).catch(() => {});
    return () => { alive = false; };
  }, [doc.id]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs]);

  const send = async (text: string) => {
    const message = text.trim();
    if (!message) return;
    if (streaming) {
      setQueued(message);
      setInput("");
      return;
    }
    if (!cfg.chat_available) {
      ed.toast(cfg.agent.hint || "No agent is configured.", "error");
      return;
    }
    await history.current;
    setInput("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    const aid = nid();
    const started = Date.now();
    setMsgs((m) => [...m, { id: nid(), role: "user", content: message, tools: [] },
      { id: aid, role: "assistant", content: "", tools: [], startedAt: started }]);
    setStreaming(true);
    ed.setAgentRunning(true);
    const ctrl = new AbortController();
    abort.current = ctrl;
    const update = (fn: (m: Msg) => Msg) => setMsgs((all) => all.map((m) => (m.id === aid ? fn(m) : m)));
    let lastMid: string | undefined;
    try {
      await streamChat(doc.id, message, (f: Frame) => {
        if (f.type === "content") {
          const sep = lastMid && f.message_id && f.message_id !== lastMid ? "\n\n" : "";
          lastMid = f.message_id || lastMid;
          update((m) => ({ ...m, content: m.content + (m.content ? sep : "") + f.content }));
        } else if (f.type === "tool_start") {
          update((m) => ({ ...m, tools: [...m.tools, { id: f.id, name: f.name, args: f.args, status: "running" }] }));
        } else if (f.type === "tool_end") {
          update((m) => ({ ...m, tools: m.tools.map((t) => (t.id === f.id ? {
            ...t, status: f.status === "error" ? "error" : "success",
            result: typeof f.result === "string" ? f.result : f.result != null ? JSON.stringify(f.result) : undefined,
            durationMs: f.duration_ms } : t)) }));
          ed.refresh();
        } else if (f.type === "error") {
          setMsgs((all) => [...all, { id: nid(), role: "error", content: f.error || f.message || `${agentName} ran into an error`, tools: [] }]);
        }
      }, ctrl.signal);
    } catch (e: any) {
      if (e?.name !== "AbortError") setMsgs((all) => [...all, { id: nid(), role: "error", content: String(e?.message || e), tools: [] }]);
    } finally {
      update((m) => ({ ...m, durationMs: Date.now() - started }));
      setStreaming(false);
      ed.setAgentRunning(false);
      abort.current = null;
      ed.refresh();
    }
  };

  useImperativeHandle(ref, () => ({ send }), [send]);

  useEffect(() => {
    if (!streaming && queued) {
      const q = queued;
      setQueued(null);
      send(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming, queued]);

  const openComments = doc.comments.filter((c) => c.status === "open").length;
  const starters = [
    openComments ? `Address all comments (${openComments})` : null,
    "Look at the layout and fix what looks off",
    "List the references",
    "Tighten the writing without changing the meaning",
  ].filter(Boolean) as string[];
  if (cfg.ui?.starter_prompts?.length) starters.splice(openComments ? 1 : 0, 3, ...cfg.ui.starter_prompts);

  return (
    <div className="flex flex-col h-full bg-[var(--color-surface)]">
      <div ref={scrollRef} className="flex-1 overflow-y-auto" data-testid="chat-log">
        <div className="px-4 py-5 space-y-5">
          {!msgs.length && (
            <div className="flex flex-col items-center justify-center py-10 gap-4">
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-[var(--color-primary)] to-[var(--color-primary-dark)] flex items-center justify-center shadow-[var(--shadow-sm)]">
                <Sparkles size={22} className="text-white" />
              </div>
              <div className="text-center">
                <p className="text-base font-semibold text-[var(--color-text)]">How can I help with this document?</p>
                <p className="text-sm text-[var(--color-text-muted)] mt-1 max-w-[280px]">
                  Every change I make to your text arrives as a suggestion you approve
                  {doc.settings.approval === "auto" ? " (auto-apply is on)" : ""}.
                </p>
              </div>
            </div>
          )}
          {msgs.map((m, i) => {
            if (m.role === "error") {
              return (
                <div key={m.id} className="text-xs rounded-md px-3 py-2 text-[var(--color-error)] bg-[color-mix(in_srgb,var(--color-error)_10%,transparent)]">
                  {m.content}</div>
              );
            }
            const showLabel = i === 0 || msgs[i - 1].role !== m.role;
            const live = streaming && m.id === msgs[msgs.length - 1]?.id;
            return (
              <div key={m.id} className="group flex gap-3">
                <div className="flex-shrink-0 w-7">{showLabel && <Avatar role={m.role} />}</div>
                <div className="flex-1 min-w-0">
                  {showLabel && (
                    <div className="flex items-baseline gap-2 mb-1 h-7">
                      <span className="text-[13px] font-semibold text-[var(--color-text)] leading-7">
                        {m.role === "user" ? "You" : agentName}</span>
                      {m.role === "assistant" && m.durationMs != null && m.durationMs >= 1 && (
                        <span className="text-[10px] text-[var(--color-text-muted)] tabular-nums">{fmtDuration(m.durationMs)}</span>
                      )}
                    </div>
                  )}
                  {m.tools.length > 0 && (
                    <div className={`space-y-1.5 ${m.content ? "mb-2" : ""}`}>
                      {m.tools.map((t) => <ToolCallCard key={t.id} tc={t} />)}
                    </div>
                  )}
                  {m.content && (
                    m.role === "user"
                      ? <p className="text-[14px] text-[var(--color-text)] leading-relaxed whitespace-pre-wrap">{m.content}</p>
                      : <div className="markdown-content text-[14px] leading-relaxed" dangerouslySetInnerHTML={{ __html: mdToHtml(m.content) }} />
                  )}
                  {live && !m.content && (
                    <span className="flex items-center gap-2 text-xs text-[var(--color-text-muted)] mt-1">
                      <span className="flex gap-1">
                        {[0, 150, 300].map((d) => (
                          <span key={d} className="w-1.5 h-1.5 rounded-full bg-[var(--color-primary)] animate-bounce" style={{ animationDelay: `${d}ms` }} />
                        ))}
                      </span>
                      {agentName} is working…
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      <div className="border-t border-[var(--color-border)] px-4 py-3">
        {!streaming && (
          <div className="flex flex-wrap gap-1.5 mb-2">
            {starters.map((q) => (
              <button key={q} disabled={!cfg.chat_available}
                onClick={() => send(q.replace(/ \(\d+\)$/, ""))}
                className="px-2.5 py-1 text-[11.5px] text-[var(--color-text-secondary)] rounded-full border border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-primary)] hover:text-[var(--color-text)] hover:bg-[var(--color-accent-soft)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
                {q}</button>
            ))}
          </div>
        )}
        {queued && (
          <div className="flex items-center gap-2 mb-2 text-[11.5px] text-[var(--color-text-secondary)]" data-testid="chat-queued">
            <span className="truncate">Next: “{queued}” (sends when {agentName} finishes)</span>
            <button className="ml-auto text-[var(--color-text-muted)] hover:text-[var(--color-text)]" title="Don't send"
              onClick={() => setQueued(null)}>✕</button>
          </div>
        )}
        <div className="flex items-end gap-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-[var(--shadow-xs)] px-3 py-2 transition-colors focus-within:border-[var(--color-primary)] focus-within:ring-1 focus-within:ring-[var(--color-primary)]">
          <textarea ref={inputRef} value={input} rows={1} data-testid="chat-input" disabled={!cfg.chat_available}
            placeholder={cfg.chat_available ? (branded ? `Ask ${branded}…` : "Ask the co-author…") : "No agent configured"}
            className="flex-1 resize-none bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none leading-relaxed"
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
            }}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
          {streaming ? (
            <button onClick={() => abort.current?.abort()} title="Stop"
              className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-md bg-[var(--color-text)] text-[var(--color-surface)] hover:opacity-80 transition-opacity">
              <Square size={12} fill="currentColor" /></button>
          ) : (
            <button onClick={() => send(input)} disabled={!input.trim()} title="Send" data-testid="chat-send"
              className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-md bg-[var(--color-text)] text-[var(--color-surface)] disabled:opacity-20 disabled:cursor-not-allowed hover:opacity-80 transition-opacity">
              <ArrowUp size={14} strokeWidth={2.5} /></button>
          )}
        </div>
        <div className={`text-center mt-1.5 transition-opacity ${input ? "opacity-0" : "opacity-100"}`}>
          <span className="text-[10px] text-[var(--color-text-muted)]">
            <kbd className="kbd">Enter</kbd> to {streaming ? "queue" : "send"}, <kbd className="kbd">Shift+Enter</kbd> for new line
          </span>
        </div>
      </div>
    </div>
  );
});
