import type React from "react";
import { Allotment } from "allotment";
import "allotment/dist/style.css";
import {
  BarChart3, ChevronDown, Download, Eye, FileText, Film, GitPullRequestArrow, Heading, Image, Lightbulb,
  ListOrdered, MessageSquare, MessagesSquare, Minus, Palette, PanelRightClose, PanelRightOpen, Plus,
  SeparatorHorizontal, Settings2, SquareDashed, SquareDashedMousePointer, Table, ZoomIn, ZoomOut,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { agentName } from "../branding";
import type { AppConfig, Doc } from "../types";
import { BlockPanel } from "./BlockPanel";
import { Canvas } from "./Canvas";
import { ChatPanel, type ChatHandle } from "./ChatPanel";
import { CommentsPanel } from "./CommentsPanel";
import { EditorContext, type EditorApi, type PanelTab } from "./context";
import { DesignPanel } from "./DesignPanel";
import { DocPanel } from "./DocPanel";
import { ReviewPanel } from "./ReviewPanel";
import { useLive } from "./useLive";
import { withBase } from "../base";
import { baseThemeName } from "../render/theme";

const ADD_TYPES: { type: string; label: string; icon: React.JSX.Element; content?: any; layout?: any }[] = [
  { type: "text", label: "Text", icon: <FileText size={14} /> },
  { type: "heading", label: "Heading", icon: <Heading size={14} />, content: { text: "", level: 2 } },
  { type: "image", label: "Image or sketch", icon: <Image size={14} /> },
  { type: "video", label: "Video or GIF", icon: <Film size={14} /> },
  { type: "table", label: "Table", icon: <Table size={14} />, content: { rows: [["Column A", "Column B"], ["", ""]], header: true } },
  { type: "chart", label: "Chart", icon: <BarChart3 size={14} />, content: { spec: {
    data: { values: [{ x: "A", y: 28 }, { x: "B", y: 55 }, { x: "C", y: 43 }] },
    mark: "bar", encoding: { x: { field: "x", type: "nominal" }, y: { field: "y", type: "quantitative" } } } } },
  { type: "callout", label: "Callout", icon: <Lightbulb size={14} /> },
  { type: "divider", label: "Divider", icon: <Minus size={14} /> },
  { type: "references", label: "References", icon: <ListOrdered size={14} /> },
  { type: "page-break", label: "Page break", icon: <SeparatorHorizontal size={14} /> },
  { type: "spacer", label: "Reserved area", icon: <SquareDashed size={14} />,
    layout: { pin: { anchor: "page", page: 1, x: 96, y: 96, w: 240, h: 160 } } },
];

const EXPORTS: [string, string, string][] = [
  ["pdf", "PDF", "vector, one page per page"],
  ["docx", "Word (.docx)", "editable, reflows"],
  ["pptx", "PowerPoint (.pptx)", "one slide per page"],
  ["png", "PNG", "one image per page"],
  ["jpg", "JPG", "one image per page"],
  ["html", "HTML (zip)", "self-contained web page"],
  ["gif", "GIF", "animated page flip"],
  ["mp4", "MP4 video", "page slideshow"],
];

/** langstage-web's right-panel pill tab. */
function TabButton({ active, onClick, icon: Icon, label, count, testid }: {
  active: boolean; onClick: () => void; icon: typeof MessageSquare; label: string; count?: number; testid: string;
}) {
  return (
    <button onClick={onClick} data-testid={testid} title={label}
      className={`flex items-center gap-1.5 flex-shrink-0 px-2 h-7 rounded-md text-[13px] font-medium whitespace-nowrap transition-colors ${
        active ? "bg-[var(--color-surface-3)] text-[var(--color-text)]"
          : "text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-3)]"}`}>
      <Icon size={14} />
      <span className={active ? "" : "ed-tab-label"}>{label}</span>
      {count != null && count > 0 && (
        <span className="min-w-[16px] h-4 px-1 rounded-full bg-[var(--color-card)] border border-[var(--color-border)] text-[10px] tabular-nums text-[var(--color-text-muted)] inline-flex items-center justify-center">
          {count}
        </span>
      )}
    </button>
  );
}

export function DocEditor({ docId, cfg, identity, onTitle, onChanged, pendingAsk, clearAsk, reloadConfig }: {
  docId: string; cfg: AppConfig; identity: { id: string; name: string; color: string; setName: (n: string) => void; managed?: boolean };
  onTitle: (t: string) => void; onChanged: () => void; pendingAsk?: string; clearAsk: () => void;
  reloadConfig?: () => Promise<void>;
}) {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selected, select] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [tab, setTab] = useState<PanelTab>(cfg.ui?.features?.chat === false ? "comments" : "chat");
  const [commentFilter, setCommentFilter] = useState<string | null>(null);
  const [agentRunning, setAgentRunning] = useState(false);
  const [toasts, setToasts] = useState<{ id: number; msg: string; kind: string }[]>([]);
  const [zoom, setZoom] = useState(1);
  const [addOpen, setAddOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [preview, setPreview] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const chatRef = useRef<ChatHandle>(null);
  const revRef = useRef(-1);
  const editingRef = useRef<string | null>(null);
  editingRef.current = editing;
  const scrollRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const d = await api.getDoc(docId);
      revRef.current = d.rev;
      setDoc(d);
      onTitle(d.title);
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  }, [docId, onTitle]);

  useEffect(() => { refresh(); }, [refresh]);

  // Live: the server pushes the revision; anyone's change (another tab, a person, the
  // agent, an MCP client) re-renders this tab. Hold off while this tab is mid-edit.
  const pendingRev = useRef(false);
  const onRev = useCallback((rev: number) => {
    if (rev === revRef.current) return;
    if (editingRef.current) {
      pendingRev.current = true;
      return;
    }
    refresh();
    onChanged();
  }, [refresh, onChanged]);
  const { peers, running: serverRunning, connected } = useLive(docId, identity, selected, editing, onRev);
  useEffect(() => {
    if (!editing && pendingRev.current) {
      pendingRev.current = false;
      refresh();
    }
  }, [editing, refresh]);

  useEffect(() => {
    if (!preview) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPreview(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview]);

  // Fit the page width to the canvas, and keep fitting as panes resize, until the
  // author picks a zoom themselves.
  const userZoomed = useRef(false);
  const pageWide = doc ? doc.settings.page_size === "slide" || doc.settings.orientation === "landscape" : false;
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const fit = () => {
      if (userZoomed.current || !el.clientWidth) return;
      const pageW = pageWide ? 1100 : 816;
      setZoom(Math.max(0.4, Math.min(1, Math.floor(((el.clientWidth - 64) / pageW) * 20) / 20)));
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [doc?.id, pageWide]);
  const zoomBy = (d: number) => {
    userZoomed.current = true;
    setZoom((z) => Math.max(0.4, Math.min(2, +(z + d).toFixed(2))));
  };

  const toast = useCallback((msg: string, kind = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 6000 : 3500);
  }, []);

  const run = useCallback(async <T,>(p: Promise<T>, okMsg?: string) => {
    try {
      const r = await p;
      await refresh();
      onChanged();
      if (okMsg) toast(okMsg);
      if (r && typeof r === "object" && (r as any).conflict) {
        toast("Someone else changed this block while you were editing. Both versions are kept: see the block's History.");
      }
      return r;
    } catch (e: any) {
      toast(String(e.message || e), "error");
      await refresh();
      return undefined;
    }
  }, [refresh, toast, onChanged]);

  const ask = useCallback((message: string) => {
    setPanelOpen(true);
    setTab("chat");
    setTimeout(() => chatRef.current?.send(message), 30);
  }, []);

  useEffect(() => {
    if (pendingAsk && doc) {
      ask(pendingAsk);
      clearAsk();
    }
  }, [pendingAsk, doc, ask, clearAsk]);

  const me = useMemo(() => ({ id: identity.id, name: identity.name, color: identity.color }),
    [identity.id, identity.name, identity.color]);

  const apiValue: EditorApi | null = useMemo(() => (doc ? {
    doc, cfg, refresh, selected, select: (id) => { select(id); if (id && tab !== "chat" && tab !== "comments") setTab("block"); },
    editing, setEditing, tab, setTab: (t) => { setPanelOpen(true); setTab(t); }, commentFilter, setCommentFilter, ask, run, toast,
    reloadConfig,
    agentRunning: agentRunning || serverRunning, setAgentRunning, zoom, peers, me,
  } : null), [doc, cfg, refresh, selected, editing, tab, commentFilter, ask, run, toast, agentRunning, serverRunning, zoom, peers, me, reloadConfig]);

  if (err) return <div className="p-8 text-[var(--color-error)]">Could not open the document: {err}</div>;
  if (!doc || !apiValue) return <div className="p-8 text-[var(--color-text-muted)]">Loading…</div>;

  const addBlock = async (t: (typeof ADD_TYPES)[number]) => {
    setAddOpen(false);
    const sel = doc.blocks.find((b) => b.id === selected);
    const flow = doc.blocks.filter((x) => x.layout.role === "flow");
    const last = flow[flow.length - 1];
    const beforeRefs = !sel && last?.type === "references" && t.type !== "references";
    const after = sel ? sel.id : beforeRefs ? (flow.length > 1 ? flow[flow.length - 2].id : null) : "__end__";
    const b = await run(api.addBlock(doc.id, { type: t.type, content: t.content, after,
      layout: t.layout ?? (sel && sel.layout.role !== "flow" ? { role: sel.layout.role } : undefined) }));
    if (b) {
      select(b.id);
      if (["text", "heading", "callout"].includes(t.type)) setEditing(b.id);
      else { setPanelOpen(true); setTab("block"); }
    }
  };

  const doExport = async (fmt: string) => {
    setExportOpen(false);
    toast(`Rendering ${fmt.toUpperCase()}…`);
    try {
      const r = await fetch(withBase(`/api/docs/${doc.id}/export?format=${fmt}`));
      if (!r.ok) {
        const j = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(j.detail);
      }
      const blob = await r.blob();
      const disp = r.headers.get("content-disposition") || "";
      const name = disp.match(/filename="([^"]+)"/)?.[1] || `document.${fmt}`;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    } catch (e: any) {
      toast(`Export failed: ${e.message}. Tip: "Print from browser" works without the renderer.`, "error");
    }
  };

  const pending = doc.suggestions.filter((s) => s.status === "pending").length;
  const openComments = doc.comments.filter((c) => c.status === "open").length;

  return (
    <EditorContext.Provider value={apiValue}>
      <div className="flex flex-col h-full min-h-0">
        {/* Toolbar */}
        <div className="flex items-center gap-2 px-3 h-11 bg-[var(--color-surface)] border-b border-[var(--color-border)] flex-none"
          onClick={(e) => e.stopPropagation()}>
          <div className="relative">
            <button className="btn btn-primary" data-testid="add-block" onClick={() => setAddOpen(!addOpen)}>
              <Plus size={14} /> Add block <ChevronDown size={12} /></button>
            {addOpen && (
              <div className="menu absolute z-40 mt-1 w-52">
                {ADD_TYPES.map((t) => (
                  <button key={t.type} data-testid={`add-${t.type}`} className="menu-item" onClick={() => addBlock(t)}>
                    <span className="text-[var(--color-text-muted)]">{t.icon}</span> {t.label}</button>
                ))}
                <div className="text-[11px] text-[var(--color-text-muted)] px-2 pt-1.5 pb-1 border-t border-[var(--color-border)] mt-1">
                  {selected ? "Inserts after the selected block" : "Adds at the end (before References)"}
                </div>
              </div>
            )}
          </div>
          <span className="chip" title="Layout">{doc.settings.mode === "pageless" ? "Pageless" : `${doc.settings.page_size.toUpperCase()} · ${doc.settings.columns} col`}</span>
          <span className="chip">{cfg.themes[baseThemeName(cfg.themes, doc.settings, cfg.default_theme) ?? ""]?.label || doc.settings.theme}</span>
          {doc.settings.approval === "auto" && <span className="chip chip-warn">auto-apply on</span>}
          <div className="ml-auto flex items-center gap-1.5">
            <Presence peers={peers} me={identity} connected={connected} />
            <div className="w-px h-4 bg-[var(--color-border)] mx-1" />
            <button className="btn btn-ghost btn-icon" title="Zoom out" onClick={() => zoomBy(-0.1)}><ZoomOut size={15} /></button>
            <span className="text-xs text-[var(--color-text-muted)] w-10 text-center tabular-nums" data-testid="zoom-label">{Math.round(zoom * 100)}%</span>
            <button className="btn btn-ghost btn-icon" title="Zoom in" onClick={() => zoomBy(0.1)}><ZoomIn size={15} /></button>
            <button className="btn" data-testid="preview" onClick={() => setPreview(true)}><Eye size={14} /> Preview</button>
            <div className="relative">
              <button className="btn" data-testid="export" onClick={() => setExportOpen(!exportOpen)}><Download size={14} /> Export <ChevronDown size={12} /></button>
              {exportOpen && (
                <div className="menu absolute right-0 z-40 mt-1 w-64">
                  {EXPORTS.map(([f, l, hint]) => (
                    <button key={f} data-testid={`export-${f}`} className="menu-item justify-between" onClick={() => doExport(f)}>
                      <span>{l}</span><span className="text-[11px] text-[var(--color-text-muted)]">{hint}</span></button>
                  ))}
                  <button className="menu-item border-t border-[var(--color-border)] mt-1 rounded-none"
                    onClick={() => { setExportOpen(false); window.open(withBase(`/print/${doc.id}?autoprint=1`), "_blank"); }}>Print from browser…</button>
                </div>
              )}
            </div>
            <button onClick={() => setPanelOpen((v) => !v)} className="p-1 rounded hover:bg-[var(--color-surface-3)] transition-colors ml-1"
              title={panelOpen ? "Hide the side panel" : "Show the side panel"} data-testid="toggle-panel">
              {panelOpen ? <PanelRightClose size={15} className="text-[var(--color-text-secondary)]" />
                : <PanelRightOpen size={15} className="text-[var(--color-text-secondary)]" />}
            </button>
          </div>
        </div>
        <div className="flex-1 min-h-0">
          <Allotment>
            <Allotment.Pane minSize={420}>
              <div ref={scrollRef} className="h-full overflow-auto cd-canvas" data-testid="canvas"
                onClick={() => { select(null); setAddOpen(false); setExportOpen(false); }}>
                {!doc.blocks.length && (
                  <div className="text-center text-[var(--color-text-muted)] mt-10">
                    <div className="text-[15px] font-medium text-[var(--color-text)]">An empty page</div>
                    <div className="mt-1">Add a block, or ask {agentName(cfg.ui)} to propose an outline.</div>
                  </div>
                )}
                <div style={{ height: "fit-content" }}><Canvas /></div>
              </div>
            </Allotment.Pane>
            <Allotment.Pane minSize={340} preferredSize={480} visible={panelOpen}>
              <div className="flex flex-col h-full bg-[var(--color-panel)]">
                <div className="ed-tabbar flex items-center gap-1 px-2 h-11 border-b border-[var(--color-border)] overflow-x-auto flex-none">
                  {cfg.ui?.features?.chat !== false && <TabButton testid="tab-chat" active={tab === "chat"} onClick={() => setTab("chat")} icon={MessageSquare} label="Chat" />}
                  <TabButton testid="tab-comments" active={tab === "comments"} onClick={() => setTab("comments")} icon={MessagesSquare} label="Comments" count={openComments} />
                  <TabButton testid="tab-review" active={tab === "review"} onClick={() => setTab("review")} icon={GitPullRequestArrow} label="Review" count={pending} />
                  <TabButton testid="tab-block" active={tab === "block"} onClick={() => setTab("block")} icon={SquareDashedMousePointer} label="Block" />
                  <TabButton testid="tab-design" active={tab === "design"} onClick={() => setTab("design")} icon={Palette} label="Design" />
                  <TabButton testid="tab-document" active={tab === "document"} onClick={() => setTab("document")} icon={Settings2} label="Document" />
                </div>
                <div className="flex-1 min-h-0">
                  <div className={tab === "chat" ? "h-full" : "hidden"}><ChatPanel ref={chatRef} /></div>
                  {tab === "comments" && <CommentsPanel />}
                  {tab === "review" && <ReviewPanel />}
                  {tab === "block" && <BlockPanel />}
                  {tab === "design" && <DesignPanel />}
                  {tab === "document" && <DocPanel identity={identity} />}
                </div>
              </div>
            </Allotment.Pane>
          </Allotment>
        </div>
      </div>
      {preview && (
        <div className="fixed inset-0 z-50 bg-black/50 flex flex-col" onClick={() => setPreview(false)}>
          <div className="flex items-center gap-3 px-4 h-11 bg-[var(--color-surface)] border-b border-[var(--color-border)]" onClick={(e) => e.stopPropagation()}>
            <Eye size={14} className="text-[var(--color-text-muted)]" />
            <span className="font-medium text-[var(--color-text)]">Preview</span>
            <span className="text-xs text-[var(--color-text-muted)]">exactly what export renders · Esc to close</span>
            <button className="btn ml-auto" onClick={() => setPreview(false)}>Close</button>
          </div>
          <iframe title="preview" src={withBase(`/print/${doc.id}`)} className="flex-1 bg-[var(--ui-canvas)]" data-testid="preview-frame" />
        </div>
      )}
      <div className="fixed bottom-4 right-4 z-[60] space-y-2">
        {toasts.map((t) => (
          <div key={t.id} className={`px-3 py-2 rounded-lg shadow-[var(--shadow-md)] text-[12.5px] max-w-sm border ${
            t.kind === "error" ? "bg-[var(--color-error)] border-transparent text-white"
              : "bg-[var(--color-card)] border-[var(--color-border)] text-[var(--color-text)]"}`}>{t.msg}</div>
        ))}
      </div>
    </EditorContext.Provider>
  );
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") || "?";
}

/** Who's here: overlapping avatars (you last), with a live/offline dot. */
function Presence({ peers, me, connected }: { peers: { client_id: string; name: string; color: string; editing: boolean }[];
  me: { name: string; color: string }; connected: boolean }) {
  return (
    <div className="flex items-center" data-testid="presence">
      <span className={`w-1.5 h-1.5 rounded-full mr-2 ${connected ? "bg-[var(--color-success)]" : "bg-[var(--color-text-muted)]"}`}
        title={connected ? "Live: changes from others appear instantly" : "Reconnecting…"} />
      <div className="flex -space-x-1.5">
        {peers.slice(0, 5).map((p) => (
          <span key={p.client_id} className="avatar" style={{ background: p.color }} data-testid="peer-avatar"
            title={`${p.name}${p.editing ? " (editing)" : ""}`}>{initials(p.name)}</span>
        ))}
        {peers.length > 5 && <span className="avatar" style={{ background: "var(--color-text-muted)" }}>+{peers.length - 5}</span>}
        <span className="avatar" style={{ background: me.color }} title={`${me.name} (you)`}>{initials(me.name)}</span>
      </div>
    </div>
  );
}
