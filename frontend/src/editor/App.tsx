import { Copy, FilePlus2, FileText, PanelLeftClose, PanelLeftOpen, PenLine, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { AppConfig, DocSummary } from "../types";
import { DocEditor } from "./DocEditor";
import { NewDocDialog } from "./NewDocDialog";
import { ThemeToggle } from "./ThemeToggle";
import { useIdentity } from "./useLive";
import { IN_FRAME, appPath, notifyHost, withBase } from "../base";
import { SessionExpired, setAuthor } from "../api";
import { applyBranding, feature, isEmbedded } from "../branding";

const TABS_KEY = "codraft.tabs";

function loadTabs(): string[] {
  try {
    return JSON.parse(localStorage.getItem(TABS_KEY) || "[]");
  } catch {
    return [];
  }
}

function docFromPath(): string | null {
  return appPath().match(/^\/d\/([\w-]+)/)?.[1] ?? null;
}

/** The family's brand tile: langstage-web's gradient square, with Codraft's pen. */
export function BrandTile({ size = 24, icon }: { size?: number; icon?: string }) {
  const [broken, setBroken] = useState(false);
  if (icon && !broken) return <img src={withBase(icon)} alt="" onError={() => setBroken(true)}
    className="flex-none object-contain" style={{ width: size, height: size }} />;
  return (
    <div className="rounded-md bg-gradient-to-br from-[var(--color-primary)] to-[var(--color-primary-dark)] flex items-center justify-center flex-none"
      style={{ width: size, height: size }}>
      <PenLine size={Math.round(size * 0.55)} className="text-white" />
    </div>
  );
}

export function App() {
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [docs, setDocs] = useState<DocSummary[]>([]);
  const [tabs, setTabs] = useState<string[]>(loadTabs);
  const [active, setActive] = useState<string | null>(docFromPath());
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [newOpen, setNewOpen] = useState(false);
  // The document list starts closed on narrow screens, where it would cover the page.
  const [sidebar, setSidebar] = useState(() => typeof window === "undefined" || window.innerWidth >= 900);
  const [kickoff, setKickoff] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [sessionLost, setSessionLost] = useState(false);
  useEffect(() => {
    // Background refreshes hit the lapsed session too; one banner, no unhandled errors.
    const lost = () => setSessionLost(true);
    const swallow = (e: PromiseRejectionEvent) => { if (e.reason instanceof SessionExpired) e.preventDefault(); };
    window.addEventListener("codraft:session-expired", lost);
    window.addEventListener("unhandledrejection", swallow);
    return () => {
      window.removeEventListener("codraft:session-expired", lost);
      window.removeEventListener("unhandledrejection", swallow);
    };
  }, []);
  const identity = useIdentity(cfg?.user?.name);
  useEffect(() => { setAuthor(identity.name); }, [identity.name]);

  const reloadDocs = useCallback(() => api.listDocs().then((d) => {
    setDocs(d);
    setTitles((t) => ({ ...Object.fromEntries(d.map((x) => [x.id, x.title])), ...t }));
    return d;
  }), []);

  // Keep the sidebar's block counts and dates fresh when the open document changes (#49).
  const reloadTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onDocChanged = useCallback(() => {
    if (reloadTimer.current) clearTimeout(reloadTimer.current);
    reloadTimer.current = setTimeout(() => { reloadDocs(); }, 400);
  }, [reloadDocs]);

  useEffect(() => {
    api.config().then((c) => { applyBranding(c.ui); setCfg(c); }).catch((e) => setErr(String(e.message || e)));
    reloadDocs();
    const onPop = () => setActive(docFromPath());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [reloadDocs]);

  useEffect(() => {
    try { localStorage.setItem(TABS_KEY, JSON.stringify(tabs)); } catch { /* private mode */ }
  }, [tabs]);

  useEffect(() => {
    if (active && !tabs.includes(active)) setTabs((t) => [...t, active]);
    const want = active ? `/d/${active}` : "/";
    if (appPath() !== want) {
      // In a frame, replace rather than push: the host page's Back button should leave the
      // page, not step through documents. The host hears where we are and can deep-link it.
      const url = withBase(want) + window.location.search;
      if (IN_FRAME) window.history.replaceState({}, "", url);
      else window.history.pushState({}, "", url);
    }
    notifyHost({ type: "codraft:location", path: want, doc: active, title: active ? titles[active] || "" : "" });
    const app = cfg?.ui?.title || cfg?.title || "Codraft";
    document.title = active ? `${titles[active] || "Document"} · ${app}` : app;
  }, [active, tabs, titles, cfg]);

  useEffect(() => {
    if (!docs.length) return;
    const ids = new Set(docs.map((d) => d.id));
    setTabs((t) => t.filter((x) => ids.has(x) || x === active));
  }, [docs, active]);

  const open = (id: string) => setActive(id);
  const close = (id: string) => {
    const rest = tabs.filter((t) => t !== id);
    setTabs(rest);
    if (active === id) setActive(rest[rest.length - 1] ?? null);
  };
  const onTitle = useCallback((id: string) => (t: string) => setTitles((m) => (m[id] === t ? m : { ...m, [id]: t })), []);

  if (err) return <div className="p-8 text-[var(--color-error)]">Could not reach the Codraft server: {err}</div>;
  if (!cfg) return <div className="p-8 text-[var(--color-text-muted)]">Loading…</div>;
  const ui = cfg.ui ?? {};
  const embedded = isEmbedded(ui);
  const docList = feature(ui, "doc_list");
  const showSidebar = sidebar && docList;

  return (
    <div className="h-full flex flex-col bg-[var(--color-surface)]">
      {sessionLost && (
        <div role="alert" data-testid="session-lost"
          className="flex-none px-4 py-2 text-[13px] text-[var(--color-warning)] bg-[color-mix(in_srgb,var(--color-warning)_12%,transparent)] border-b border-[var(--color-border)]">
          {new SessionExpired().message}
        </div>
      )}
      {/* Header: langstage-web's quiet 44px bar */}
      <header data-testid="app-header" className={`flex items-center gap-3 px-4 border-b border-[var(--color-border)] bg-[var(--ui-header,var(--color-surface))] text-[var(--ui-header-text,var(--color-text))] flex-none ${embedded ? "h-10" : "h-11"}`}>
        {docList && <button onClick={() => setSidebar(!sidebar)} data-testid="toggle-doc-list"
          className="p-1 rounded hover:bg-[var(--color-surface-3)] transition-colors"
          title={sidebar ? "Hide the document list" : "Show the document list"}>
          {sidebar ? <PanelLeftClose size={15} className="text-[var(--color-text-secondary)]" />
            : <PanelLeftOpen size={15} className="text-[var(--color-text-secondary)]" />}
        </button>}
        {!embedded && (
          <div className="flex items-center gap-2.5" data-testid="brand">
            <BrandTile icon={ui.icon_url} />
            <h1 className="text-sm font-semibold tracking-tight">{ui.title || cfg.title}</h1>
            <span className="text-[11px] text-[var(--color-text-muted)] hidden md:inline border-l border-[var(--color-border)] pl-2.5">
              {ui.subtitle || "the document stage"}
            </span>
          </div>
        )}
        {/* Open documents as pill tabs */}
        <div className="flex items-center gap-1 ml-3 min-w-0 overflow-x-auto">
          {tabs.map((id) => (
            <div key={id} onClick={() => open(id)} data-testid="doc-tab"
              className={`group flex items-center gap-1.5 flex-shrink-0 pl-2.5 pr-1 h-7 rounded-md text-[13px] font-medium cursor-pointer max-w-[220px] transition-colors ${
                id === active ? "bg-[var(--color-surface-3)] text-[var(--color-text)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-3)]"}`}>
              <FileText size={13} className="flex-none" />
              <span className="truncate">{titles[id] || "Document"}</span>
              <button className="opacity-50 hover:opacity-100 p-0.5 rounded" title="Close tab"
                onClick={(e) => { e.stopPropagation(); close(id); }}><X size={12} /></button>
            </div>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-[11px] text-[var(--color-text-muted)] truncate max-w-[260px] hidden lg:inline"
            title={cfg.agent.hint || cfg.agent.label}>
            <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 align-middle ${cfg.chat_available ? "bg-[var(--color-success)]" : "bg-[var(--color-text-muted)]"}`} />
            {cfg.agent.label}
          </span>
          {!embedded && feature(ui, "theme_toggle") && <div className="w-px h-4 bg-[var(--color-border)]" />}
          <ThemeToggle initialTheme={ui.theme || cfg.ui_theme} fixed={embedded || !feature(ui, "theme_toggle")} />
        </div>
      </header>
      <div className="flex flex-1 min-h-0">
        {showSidebar && (
          <aside className="w-60 flex-none bg-[var(--color-panel)] border-r border-[var(--color-border)] flex flex-col">
            <div className="p-3">
              <button className="btn btn-primary w-full justify-center" data-testid="new-doc" onClick={() => setNewOpen(true)}>
                <FilePlus2 size={14} /> New document</button>
            </div>
            <div className="px-4 pb-1 text-[11px] font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Documents</div>
            <div className="flex-1 overflow-auto scroll-thin py-1">
              {!docs.length && <div className="px-4 py-2 text-xs text-[var(--color-text-muted)]">Nothing yet.</div>}
              {docs.map((d) => (
                <div key={d.id} onClick={() => open(d.id)} data-testid="doc-list-item"
                  className={`group mx-2 px-2.5 py-2 rounded-md cursor-pointer transition-colors ${
                    d.id === active ? "bg-[var(--color-card)] shadow-[var(--shadow-xs)] border border-[var(--color-border)]"
                      : "border border-transparent hover:bg-[var(--color-surface-3)]"}`}>
                  <div className="flex items-center gap-1">
                    <div className="truncate flex-1 font-medium text-[13px] text-[var(--color-text)]">{titles[d.id] || d.title}</div>
                    <button className="hidden group-hover:block text-[var(--color-text-muted)] hover:text-[var(--color-text)]" title="Duplicate"
                      onClick={async (e) => { e.stopPropagation(); const c = await api.duplicateDoc(d.id); await reloadDocs(); open(c.id); }}><Copy size={12} /></button>
                    <button className="hidden group-hover:block text-[var(--color-text-muted)] hover:text-[var(--color-error)]" title="Delete"
                      onClick={async (e) => {
                        e.stopPropagation();
                        if (!confirm(`Delete “${d.title}”?`)) return;
                        await api.deleteDoc(d.id);
                        close(d.id);
                        reloadDocs();
                      }}><Trash2 size={12} /></button>
                  </div>
                  <div className="text-[11px] text-[var(--color-text-muted)] tabular-nums" data-testid="doc-list-meta">
                    {d.n_blocks} blocks · {new Date(d.updated_at * 1000).toLocaleDateString()}
                  </div>
                </div>
              ))}
            </div>
          </aside>
        )}
        <main className="flex-1 min-w-0">
          {active ? (
            <DocEditor key={active} docId={active} cfg={cfg} identity={identity} onTitle={onTitle(active)}
              onChanged={onDocChanged}
              pendingAsk={kickoff[active]} clearAsk={() => setKickoff((k) => { const { [active]: _, ...rest } = k; return rest; })} />
          ) : (
            <Home cfg={cfg} onNew={() => setNewOpen(true)} docs={docs} open={open} />
          )}
        </main>
      </div>
      {newOpen && (
        <NewDocDialog cfg={cfg} onClose={() => setNewOpen(false)} onCreated={async (d, k) => {
          setNewOpen(false);
          await reloadDocs();
          if (k) setKickoff((m) => ({ ...m, [d.id]: k }));
          open(d.id);
        }} />
      )}
    </div>
  );
}

function Home({ cfg, onNew, docs, open }: { cfg: AppConfig; onNew: () => void; docs: DocSummary[]; open: (id: string) => void }) {
  return (
    <div className="h-full overflow-auto">
      <div className="flex flex-col items-center justify-center py-24 gap-5 px-6">
        <BrandTile size={48} icon={cfg.ui?.icon_url} />
        <div className="text-center">
          <p className="text-base font-semibold text-[var(--color-text)]">What are you writing?</p>
          <p className="text-sm text-[var(--color-text-muted)] mt-1 max-w-md whitespace-pre-line" data-testid="welcome">
            {cfg.ui?.welcome_message || `You own the thinking; ${cfg.ui?.agent_name && cfg.ui.agent_name !== "Co-author" ? cfg.ui.agent_name : "your agent"} owns the styling. Draft block by block, tag the agent in comments, approve every change, and export exactly what you see.`}
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-2 max-w-md">
          <button onClick={onNew} className="px-3 py-1.5 text-xs text-white rounded-full bg-[var(--color-primary)] hover:bg-[var(--color-primary-dark)] transition-colors">
            New document</button>
          {docs.slice(0, 3).map((d) => (
            <button key={d.id} onClick={() => open(d.id)}
              className="px-3 py-1.5 text-xs text-[var(--color-text-secondary)] rounded-full border border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-primary)] hover:text-[var(--color-text)] hover:bg-[var(--color-accent-soft)] transition-colors">
              {d.title}</button>
          ))}
        </div>
        {!cfg.chat_available && (
          <div className="text-xs rounded-lg p-3 max-w-md text-[var(--color-warning)] bg-[color-mix(in_srgb,var(--color-warning)_12%,transparent)]">
            No agent is configured, so the editor works but chat is off. {cfg.agent.hint}
          </div>
        )}
      </div>
    </div>
  );
}
