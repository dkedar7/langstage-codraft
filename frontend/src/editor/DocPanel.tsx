import { Check, Copy, FileText, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../api";
import { PAGE_SIZE_LABELS } from "../layout/units";
import type { Settings } from "../types";
import { NumberInput, TextInput } from "./BlockPanel";
import { useEditorApi } from "./context";
import { BRIEF_OPTIONS } from "./NewDocDialog";

export function DocPanel({ identity }: { identity: { name: string; setName: (n: string) => void } }) {
  const ed = useEditorApi();
  const { doc, cfg } = ed;
  const s = doc.settings;
  const set = (patch: Partial<Settings> | Record<string, any>) => ed.run(api.patchDoc(doc.id, { settings: patch }));
  const fileRef = useRef<HTMLInputElement>(null);
  const [purpose, setPurpose] = useState<"reference" | "template">("reference");

  return (
    <div className="overflow-auto scroll-thin h-full">
      <div className="panel-section">
        <span className="label" style={{ marginTop: 0 }}>Title</span>
        <TextInput value={doc.title} onCommit={(v) => ed.run(api.patchDoc(doc.id, { title: v }))} testid="doc-title" />
      </div>

      <div className="panel-section">
        <div className="font-semibold">Layout</div>
        <div className="grid grid-cols-2 gap-x-2">
          <div>
            <span className="label">Mode</span>
            <select className="select" value={s.mode} data-testid="mode-select" onChange={(e) => set({ mode: e.target.value as any })}>
              <option value="paged">Pages</option><option value="pageless">Pageless</option>
            </select>
          </div>
          <div>
            <span className="label">Theme</span>
            <select className="select" value={s.theme} data-testid="theme-select" onChange={(e) => set({ theme: e.target.value })}>
              {Object.entries(cfg.themes).map(([k, t]) => <option key={k} value={k}>{t.label}</option>)}
            </select>
          </div>
          {s.mode === "paged" && (
            <>
              <div>
                <span className="label">Page size</span>
                <select className="select" value={s.page_size} onChange={(e) => set({ page_size: e.target.value as any })}>
                  {Object.entries(PAGE_SIZE_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              </div>
              <div>
                <span className="label">Orientation</span>
                <select className="select" value={s.orientation} onChange={(e) => set({ orientation: e.target.value as any })}>
                  <option value="portrait">Portrait</option><option value="landscape">Landscape</option>
                </select>
              </div>
              <div>
                <span className="label">Columns</span>
                <div className="flex gap-1" data-testid="columns">
                  {[1, 2, 3].map((n) => (
                    <button key={n} className={`btn flex-1 justify-center ${s.columns === n ? "btn-primary" : ""}`}
                      onClick={() => set({ columns: n })}>{n}</button>
                  ))}
                </div>
              </div>
              <div>
                <span className="label">Pages</span>
                <div className="flex gap-1">
                  <button className="btn flex-1 justify-center" data-testid="add-page" onClick={() => set({ min_pages: (s.min_pages || 1) + 1 })}>+ Add page</button>
                  <button className="btn" disabled={(s.min_pages || 1) <= 1} onClick={() => set({ min_pages: Math.max(1, (s.min_pages || 1) - 1) })}>−</button>
                </div>
              </div>
            </>
          )}
        </div>
        <span className="label">Margins (mm): top, right, bottom, left</span>
        <div className="grid grid-cols-4 gap-1">
          {(["top", "right", "bottom", "left"] as const).map((k) => (
            <NumberInput key={k} value={s.margins[k]} min={0} max={80} onCommit={(v) => set({ margins: { ...s.margins, [k]: v } })} />
          ))}
        </div>
        <div className="grid grid-cols-2 gap-x-2">
          <div><span className="label">Gap between blocks (mm)</span>
            <NumberInput value={s.block_gap} min={0} max={30} step={0.5} onCommit={(v) => set({ block_gap: v })} /></div>
          <div><span className="label">Column gap (mm)</span>
            <NumberInput value={s.column_gap} min={0} max={30} step={0.5} onCommit={(v) => set({ column_gap: v })} /></div>
        </div>
      </div>

      <div className="panel-section">
        <div className="font-semibold">Brief</div>
        <div className="text-[11.5px] text-[var(--color-text-muted)]">The agent reads this to match your intent.</div>
        {BRIEF_OPTIONS.map((f) => (
          <div key={f.key}>
            <span className="label">{f.label}</span>
            {f.options ? (
              <select className="select" value={(s.brief as any)[f.key] || ""} onChange={(e) => set({ brief: { [f.key]: e.target.value } })}>
                <option value="">—</option>
                {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
                {(s.brief as any)[f.key] && !f.options.includes((s.brief as any)[f.key]) && <option value={(s.brief as any)[f.key]}>{(s.brief as any)[f.key]}</option>}
              </select>
            ) : (
              <TextInput value={(s.brief as any)[f.key] || ""} placeholder={f.placeholder} onCommit={(v) => set({ brief: { [f.key]: v } })} />
            )}
          </div>
        ))}
        <span className="label">Anything else the agent should know</span>
        <BriefNotes value={s.brief.notes || ""} onCommit={(v) => set({ brief: { notes: v } })} />
      </div>

      <div className="panel-section">
        <div className="font-semibold mb-1">Uploads</div>
        <div className="text-[11.5px] text-[var(--color-text-muted)] mb-2">Reference material the agent can search, or a document to use as a template.</div>
        {doc.uploads.filter((u) => u.purpose !== "asset").map((u) => (
          <div key={u.id} className="flex items-center gap-2 py-1">
            <FileText size={14} className="text-[var(--color-text-muted)]" />
            <a href={u.url} target="_blank" rel="noreferrer" className="truncate flex-1 text-[var(--ui-accent)]">{u.filename}</a>
            <span className="chip">{u.purpose}</span>
            <button className="btn btn-icon btn-ghost btn-danger" onClick={() => ed.run(api.deleteUpload(u.id))}><Trash2 size={13} /></button>
          </div>
        ))}
        <div className="flex gap-2 mt-2">
          <select className="select" style={{ width: 130 }} value={purpose} onChange={(e) => setPurpose(e.target.value as any)}>
            <option value="reference">Reference</option><option value="template">Template</option>
          </select>
          <button className="btn" onClick={() => fileRef.current?.click()}><Upload size={14} /> Upload</button>
          <input ref={fileRef} type="file" hidden data-testid="upload-input"
            accept=".pdf,.docx,.md,.txt,.html,.htm,.csv,.json" onChange={async (e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (!f) return;
              const u = await ed.run(api.upload(doc.id, f, purpose), `Uploaded ${f.name}`);
              if (u && purpose === "template" && cfg.chat_available) {
                ed.ask(`Use the uploaded template "${u.filename}" (${u.id}) to scaffold this document: mirror its structure as blocks.`);
              }
            }} />
        </div>
      </div>

      <div className="panel-section">
        <div className="font-semibold">You</div>
        <div className="text-[11.5px] text-[var(--color-text-muted)] mb-1">How collaborators see you on the page.</div>
        <TextInput value={identity.name} onCommit={identity.setName} testid="my-name" />
      </div>

      {cfg.mcp_url && (
        <div className="panel-section">
          <div className="font-semibold">Connect another agent (MCP)</div>
          <div className="text-[11.5px] text-[var(--color-text-muted)] mt-0.5 mb-2">
            Claude Code, Cursor or any MCP client can co-author this document. Its edits arrive here as suggestions.
          </div>
          <CopyLine text={`${window.location.origin}${cfg.mcp_url}`} />
          <div className="mt-2"><CopyLine text={`claude mcp add --transport http codraft ${window.location.origin}${cfg.mcp_url}`} /></div>
          <div className="text-[11px] text-[var(--color-text-muted)] mt-2">This document's id: <code className="kbd">{doc.id}</code></div>
        </div>
      )}

      <div className="panel-section">
        <div className="font-semibold">About the agent</div>
        <div className="text-xs text-[var(--color-text-secondary)] mt-1">{cfg.agent.label} <span className="chip">{cfg.agent.kind}</span></div>
        {cfg.agent.missing_tools?.length > 0 && cfg.agent.kind !== "none" && (
          <div className="text-xs text-[var(--color-warning)] bg-[color-mix(in_srgb,var(--color-warning)_12%,transparent)] rounded p-2 mt-2">
            This agent is missing {cfg.agent.missing_tools.length} Codraft tools, so it can chat but not edit the document.
          </div>
        )}
        <button className="btn btn-danger mt-3" onClick={async () => {
          if (!confirm(`Delete “${doc.title}”?`)) return;
          await api.deleteDoc(doc.id);
          window.location.href = "/";
        }}><Trash2 size={14} /> Delete document</button>
      </div>
    </div>
  );
}

function BriefNotes({ value, onCommit }: { value: string; onCommit: (v: string) => void }) {
  const [v, setV] = useState(value);
  return <textarea className="textarea" value={v} onChange={(e) => setV(e.target.value)} onBlur={() => v !== value && onCommit(v)} />;
}

function CopyLine({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="flex items-center gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-2)] pl-2">
      <code className="text-[11px] text-[var(--color-text-secondary)] truncate flex-1" title={text}>{text}</code>
      <button className="btn btn-ghost btn-icon" title="Copy" onClick={() => {
        navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }).catch(() => {});
      }}>{done ? <Check size={13} /> : <Copy size={13} />}</button>
    </div>
  );
}
