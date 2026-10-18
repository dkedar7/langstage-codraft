import { FileUp, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { PAGE_SIZE_LABELS } from "../layout/units";
import type { AppConfig, Doc } from "../types";

/** The seven inputs from the design notes (layout is captured separately). */
export const BRIEF_OPTIONS: { key: string; label: string; options?: string[]; placeholder?: string }[] = [
  { key: "writing_style", label: "Writing style", options: ["academic", "professional / formal", "casual", "informal", "persuasive", "technical"] },
  { key: "objective", label: "Objective", options: ["think", "learn", "sell", "advertise", "convince", "inform"] },
  { key: "platform", label: "Platform", options: ["email attachment", "journal", "social media", "blog", "website", "print", "presentation"] },
  { key: "audience", label: "Audience", placeholder: "e.g. engineering leadership, first-year students" },
  { key: "language", label: "Language", placeholder: "English" },
  { key: "output_format", label: "Output format", options: ["pdf", "html", "png", "jpg"] },
];

export function NewDocDialog({ cfg, onClose, onCreated }: { cfg: AppConfig; onClose: () => void; onCreated: (d: Doc, kickoff?: string) => void }) {
  const [template, setTemplate] = useState("blank");
  const [title, setTitle] = useState("");
  const [brief, setBrief] = useState<Record<string, string>>({ language: "English", output_format: "pdf" });
  const [notes, setNotes] = useState("");
  const [layout, setLayout] = useState({ mode: "paged", page_size: cfg.default_page_size || "letter", columns: 0, theme: "" });
  const [files, setFiles] = useState<{ file: File; purpose: "reference" | "template" }[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const create = async () => {
    setBusy(true);
    setErr(null);
    try {
      const settings: Record<string, any> = { mode: layout.mode, page_size: layout.page_size, brief: { ...brief, notes } };
      if (layout.columns) settings.columns = layout.columns;
      if (layout.theme) settings.theme = layout.theme;
      const doc = await api.createDoc({ title: title || undefined, template, settings });
      let kickoff: string | undefined;
      for (const f of files) {
        const u = await api.upload(doc.id, f.file, f.purpose);
        if (f.purpose === "template") kickoff = `Use the uploaded template "${u.filename}" (${u.id}) to scaffold this document: mirror its structure as blocks, with placeholder text where content is specific to the original.`;
      }
      if (!kickoff && notes.trim()) {
        kickoff = template === "blank"
          ? `Here is what I want to write: ${notes.trim()}\nPropose an outline as heading and text blocks (short placeholder text only; I will write the content).`
          : `Here is what I want to write: ${notes.trim()}\nAdapt this template's headings and placeholder text to fit it (short placeholders only; I will write the content).`;
      }
      onCreated(doc, cfg.chat_available ? kickoff : undefined);
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-6" onClick={onClose}>
      <div className="bg-[var(--color-card)] rounded-xl shadow-2xl w-full max-w-3xl max-h-full flex flex-col overflow-hidden" onClick={(e) => e.stopPropagation()}
        data-testid="new-doc-dialog">
        <div className="flex items-center px-5 py-3 border-b border-[var(--color-border)] flex-none">
          <div className="font-semibold text-[15px]">New document</div>
          <button className="btn btn-ghost btn-icon ml-auto" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="p-5 grid md:grid-cols-[1.1fr_1fr] gap-6 flex-1 min-h-0 overflow-auto scroll-thin">
          <div>
            <span className="label" style={{ marginTop: 0 }}>Start from</span>
            <div className="grid grid-cols-2 gap-2">
              {cfg.templates.map((t) => (
                <button key={t.id} data-testid={`template-${t.id}`} onClick={() => setTemplate(t.id)}
                  className={`text-left border rounded-lg p-2.5 ${template === t.id ? "border-[var(--ui-accent)] bg-[var(--ui-accent-soft)]" : "border-[var(--color-border)] hover:border-[var(--color-border)]"}`}>
                  <div className="font-medium">{t.name}</div>
                  <div className="text-[11.5px] text-[var(--color-text-muted)] leading-snug mt-0.5">{t.description}</div>
                </button>
              ))}
            </div>
            <span className="label">Title</span>
            <input className="input" value={title} placeholder="Untitled document" data-testid="new-title" onChange={(e) => setTitle(e.target.value)} />
            <span className="label">Describe it in your own words (optional)</span>
            <textarea className="textarea" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. a two-page proposal to fund a city bike-share pilot, for the transportation committee" />
            <span className="label">Upload references or a template</span>
            <label className="btn cursor-pointer"><FileUp size={14} /> Add files
              <input type="file" hidden multiple accept=".pdf,.docx,.md,.txt,.html,.htm,.csv,.json"
                onChange={(e) => setFiles([...files, ...Array.from(e.target.files || []).map((file) => ({ file, purpose: "reference" as const }))])} />
            </label>
            {files.map((f, i) => (
              <div key={i} className="flex items-center gap-2 mt-1.5 text-xs">
                <span className="truncate flex-1">{f.file.name}</span>
                <select className="select" style={{ width: 110, padding: "2px 4px" }} value={f.purpose}
                  onChange={(e) => setFiles(files.map((x, k) => (k === i ? { ...x, purpose: e.target.value as any } : x)))}>
                  <option value="reference">reference</option><option value="template">template</option>
                </select>
                <button className="btn btn-ghost btn-icon" onClick={() => setFiles(files.filter((_, k) => k !== i))}><X size={12} /></button>
              </div>
            ))}
          </div>
          <div>
            <span className="label" style={{ marginTop: 0 }}>Layout</span>
            <div className="grid grid-cols-2 gap-2">
              <select className="select" value={layout.mode} onChange={(e) => setLayout({ ...layout, mode: e.target.value })}>
                <option value="paged">Pages</option><option value="pageless">Pageless</option>
              </select>
              <select className="select" value={layout.page_size} onChange={(e) => setLayout({ ...layout, page_size: e.target.value })}>
                {Object.entries(PAGE_SIZE_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
              <select className="select" value={layout.columns} onChange={(e) => setLayout({ ...layout, columns: Number(e.target.value) })}>
                <option value={0}>Columns (template)</option>
                <option value={1}>1 column</option><option value={2}>2 columns</option><option value={3}>3 columns</option>
              </select>
              <select className="select" value={layout.theme} onChange={(e) => setLayout({ ...layout, theme: e.target.value })}>
                <option value="">Theme (template)</option>
                {Object.entries(cfg.themes).map(([k, t]) => <option key={k} value={k}>{t.label}</option>)}
              </select>
            </div>
            {BRIEF_OPTIONS.map((f) => (
              <div key={f.key}>
                <span className="label">{f.label}</span>
                {f.options ? (
                  <select className="select" value={brief[f.key] || ""} onChange={(e) => setBrief({ ...brief, [f.key]: e.target.value })}>
                    <option value="">—</option>
                    {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : (
                  <input className="input" value={brief[f.key] || ""} placeholder={f.placeholder}
                    onChange={(e) => setBrief({ ...brief, [f.key]: e.target.value })} />
                )}
              </div>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-3 px-5 py-3 border-t border-[var(--color-border)] flex-none">
          {err && <span className="text-[var(--color-error)] text-xs">{err}</span>}
          <button className="btn ml-auto" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" data-testid="create-doc" disabled={busy} onClick={create}>
            {busy ? "Creating…" : "Create document"}</button>
        </div>
      </div>
    </div>
  );
}
