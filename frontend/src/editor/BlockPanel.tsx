import { ArrowDown, ArrowUp, Bot, Copy, GitBranch, MessageSquare, PenLine, Plus, Redo2, Trash2, Undo2, Upload, Wand2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Block, Ref, VersionTree } from "../types";
import { blockText } from "./Diff";
import { useEditorApi } from "./context";
import { SketchPad } from "./SketchPad";
import { withBase } from "../base";
import { agentName, authorLabel } from "../branding";

export function BlockPanel() {
  const ed = useEditorApi();
  const block = ed.doc.blocks.find((b) => b.id === ed.selected);
  if (!block) {
    return <div className="p-4 text-[var(--color-text-muted)] text-xs">Select a block on the page to edit its content, layout, sources and history.</div>;
  }
  return <BlockInspector key={block.id + block.head} block={block} />;
}

function BlockInspector({ block }: { block: Block }) {
  const ed = useEditorApi();
  const { doc } = ed;
  const flow = doc.blocks;
  const idx = flow.findIndex((b) => b.id === block.id);
  const patchLayout = (layout: any) => ed.run(api.patchBlock(block.id, { layout }));
  const patchContent = (content: any, note = "edited") => ed.run(api.patchBlock(block.id, { content, note }));
  const L = block.layout;
  const onOwnPage = L.role !== "flow"; // cover or back page: pins place it on that page

  return (
    <div className="overflow-auto scroll-thin h-full">
      <div className="panel-section">
        <div className="flex items-center gap-2">
          <span className="chip">{block.type}</span>
          <span className="text-xs text-[var(--color-text-muted)]">last change by {block.author}</span>
        </div>
        <div className="flex flex-wrap gap-1 mt-3">
          <button className="btn btn-icon" title="Move up" disabled={idx <= 0}
            onClick={() => ed.run(api.moveBlock(block.id, idx >= 2 ? flow[idx - 2].id : null))}><ArrowUp size={14} /></button>
          <button className="btn btn-icon" title="Move down" disabled={idx >= flow.length - 1}
            onClick={() => ed.run(api.moveBlock(block.id, flow[idx + 1].id))}><ArrowDown size={14} /></button>
          <button className="btn btn-icon" title="Duplicate" onClick={() => ed.run(api.duplicateBlock(block.id))}><Copy size={14} /></button>
          <button className="btn btn-icon" title="Comment" onClick={() => { ed.setCommentFilter(block.id); ed.setTab("comments"); }}><MessageSquare size={14} /></button>
          <button className="btn" disabled={!ed.cfg.chat_available || ed.agentRunning}
            onClick={() => ed.ask(`Look at block ${block.id} and suggest how to improve it.`)}><Bot size={14} /> Ask {agentName(ed.cfg.ui, false)}</button>
          <button className="btn btn-icon btn-danger ml-auto" title="Delete block" data-testid="delete-block"
            onClick={() => { ed.select(null); ed.run(api.deleteBlock(block.id)); }}><Trash2 size={14} /></button>
        </div>
      </div>

      <Versions block={block} />

      <div className="panel-section">
        <div className="font-semibold mb-1">Content</div>
        <ContentEditor block={block} patch={patchContent} />
      </div>

      <div className="panel-section">
        <div className="font-semibold mb-1">Layout</div>
        <span className="label">Role</span>
        <select className="select" value={L.role} onChange={(e) => patchLayout({ role: e.target.value })}>
          <option value="flow">Body (flows through columns)</option>
          <option value="cover">Cover page</option>
          <option value="back">Back page</option>
        </select>
        {L.role === "flow" && !L.pin && (
          <>
            <span className="label">Width</span>
            <select className="select" value={L.span} data-testid="span-select" onChange={(e) => patchLayout({ span: e.target.value })}>
              <option value="column">One column</option>
              <option value="full">Full width (all columns)</option>
            </select>
          </>
        )}
        {(VARIANTS[block.type] || []).length > 0 && (
          <>
            <span className="label">Style</span>
            <select className="select" value={L.variant || ""} data-testid="variant-select"
              onChange={(e) => patchLayout({ variant: e.target.value || null })}>
              <option value="">Default</option>
              {VARIANTS[block.type].map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </>
        )}
        {block.type === "text" && (
          <label className="flex items-center gap-2 mt-2">
            <input type="checkbox" checked={!!L.dropcap} onChange={(e) => patchLayout({ dropcap: e.target.checked })} /> Drop cap
          </label>
        )}
        <span className="label">Height</span>
        <div className="flex gap-2 items-center">
          <select className="select" value={L.height ? "fixed" : "auto"}
            onChange={(e) => patchLayout({ height: e.target.value === "fixed" ? 160 : null })}>
            <option value="auto">Auto (grows with content)</option>
            <option value="fixed">Fixed</option>
          </select>
          {L.height ? (
            <NumberInput value={L.height} suffix="px" onCommit={(v) => patchLayout({ height: v })} />
          ) : null}
        </div>
        {(
          <>
            <label className="flex items-center gap-2 mt-3">
              <input type="checkbox" data-testid="pin-toggle" checked={!!L.pin}
                onChange={(e) => patchLayout({ pin: !e.target.checked ? null : onOwnPage
                  ? { anchor: "page", page: 1, x: 0, y: 0, w: 300, h: L.height || 200 }
                  : { page: 1, top: 0, column: 0, span: 1, height: L.height || null } })} />
              {onOwnPage ? `Pin to a spot on the ${L.role} page (art, a logo, a band)` : "Pin to a page position (other blocks flow around it)"}
            </label>
            {L.pin && (
              <label className="flex items-center gap-2 mt-2">
                <input type="checkbox" checked={L.pin.anchor === "page"} data-testid="pin-page-anchor"
                  onChange={(e) => patchLayout({ pin: e.target.checked
                    ? { anchor: "page", page: L.pin!.page, x: 0, y: 0, w: 400, h: L.pin!.height || 240 }
                    : { page: L.pin!.page, top: 0, column: 0, span: 1, height: null } })} />
                Anchor to the page (can bleed past the margins)
              </label>
            )}
            {L.pin?.anchor === "page" && (
              <div className="grid grid-cols-2 gap-2 mt-2">
                {!onOwnPage && <Field label="Page"><NumberInput value={L.pin.page} min={1} onCommit={(v) => patchLayout({ pin: { ...L.pin, page: v } })} /></Field>}
                <Field label="X"><NumberInput value={L.pin.x ?? 0} suffix="px" onCommit={(v) => patchLayout({ pin: { ...L.pin, x: v } })} /></Field>
                <Field label="Y"><NumberInput value={L.pin.y ?? 0} suffix="px" onCommit={(v) => patchLayout({ pin: { ...L.pin, y: v } })} /></Field>
                <Field label="Width"><NumberInput value={L.pin.w ?? 300} suffix="px" onCommit={(v) => patchLayout({ pin: { ...L.pin, w: v } })} /></Field>
                <Field label="Height"><NumberInput value={L.pin.h ?? 200} suffix="px" onCommit={(v) => patchLayout({ pin: { ...L.pin, h: v } })} /></Field>
              </div>
            )}
            {L.pin && L.pin.anchor !== "page" && onOwnPage && (
              <div className="grid grid-cols-2 gap-2 mt-2">
                <Field label="Top"><NumberInput value={L.pin.top} suffix="px" onCommit={(v) => patchLayout({ pin: { ...L.pin, top: v } })} /></Field>
              </div>
            )}
            {L.pin && L.pin.anchor !== "page" && !onOwnPage && (
              <div className="grid grid-cols-2 gap-2 mt-2">
                <Field label="Page"><NumberInput value={L.pin.page} min={1} onCommit={(v) => patchLayout({ pin: { ...L.pin, page: v } })} /></Field>
                <Field label="Top"><NumberInput value={L.pin.top} suffix="px" onCommit={(v) => patchLayout({ pin: { ...L.pin, top: v } })} /></Field>
                <Field label="Column"><NumberInput value={L.pin.column + 1} min={1} max={doc.settings.columns} onCommit={(v) => patchLayout({ pin: { ...L.pin, column: v - 1 } })} /></Field>
                <Field label="Span"><NumberInput value={L.pin.span} min={1} max={doc.settings.columns} onCommit={(v) => patchLayout({ pin: { ...L.pin, span: v } })} /></Field>
                <Field label="Repeat on">
                  <select className="select" data-testid="repeat-select"
                    value={Array.isArray(L.repeat) ? "list" : L.repeat || "none"}
                    onChange={(e) => {
                      const v = e.target.value;
                      patchLayout({ repeat: v === "none" ? null : v === "list" ? [L.pin!.page] : v });
                    }}>
                    <option value="none">This page only</option>
                    <option value="all">All pages</option>
                    <option value="odd">Odd pages</option>
                    <option value="even">Even pages</option>
                    <option value="list">Chosen pages…</option>
                  </select>
                </Field>
                {Array.isArray(L.repeat) && (
                  <Field label="Pages">
                    <TextInput value={L.repeat.join(", ")} onCommit={(v) => {
                      const pages = (v.match(/\d+/g) || []).map(Number).filter((n) => n >= 1);
                      patchLayout({ repeat: pages.length ? pages : null });
                    }} />
                  </Field>
                )}
              </div>
            )}
          </>
        )}
      </div>

      <References block={block} />
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><span className="label" style={{ marginTop: 0 }}>{label}</span>{children}</div>;
}

export function NumberInput({ value, onCommit, min, max, suffix, step = 1 }: {
  value: number; onCommit: (v: number) => void; min?: number; max?: number; suffix?: string; step?: number;
}) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  const commit = () => {
    let n = Number(v);
    if (!Number.isFinite(n)) return setV(String(value));
    if (min !== undefined) n = Math.max(min, n);
    if (max !== undefined) n = Math.min(max, n);
    if (n !== value) onCommit(n);
    else setV(String(value));
  };
  return (
    <div className="flex items-center gap-1">
      <input className="input" type="number" value={v} step={step} onChange={(e) => setV(e.target.value)} onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()} />
      {suffix && <span className="text-xs text-[var(--color-text-muted)]">{suffix}</span>}
    </div>
  );
}

export function TextInput({ value, onCommit, placeholder, testid }: { value: string; onCommit: (v: string) => void; placeholder?: string; testid?: string }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <input className="input" value={v} placeholder={placeholder} data-testid={testid} onChange={(e) => setV(e.target.value)}
      onBlur={() => v !== value && onCommit(v)} onKeyDown={(e) => e.key === "Enter" && v !== value && onCommit(v)} />
  );
}

function Versions({ block }: { block: Block }) {
  const ed = useEditorApi();
  const [tree, setTree] = useState<VersionTree | null>(null);
  useEffect(() => {
    api.versions(block.id).then(setTree).catch(() => setTree(null));
  }, [block.id, block.head]);
  if (!tree) return null;
  const by = Object.fromEntries(tree.versions.map((v) => [v.id, v]));
  const head = by[tree.head];
  // the path from root to head, plus siblings to show branches
  const path: string[] = [];
  for (let cur = head; cur; cur = cur.parent_id ? by[cur.parent_id] : (undefined as any)) path.unshift(cur.id);
  const canBack = !!head?.parent_id;
  const canFwd = !!head?.children.length;
  return (
    <div className="panel-section">
      <div className="flex items-center gap-2">
        <div className="font-semibold">History</div>
        <span className="chip">{tree.versions.length} versions</span>
        {tree.branches.length > 1 && <span className="chip"><GitBranch size={10} /> {tree.branches.length} branches</span>}
        <div className="ml-auto flex gap-1">
          <button className="btn btn-icon" title="Previous version" data-testid="version-back" disabled={!canBack}
            onClick={() => ed.run(api.checkout(block.id, { direction: "back" }))}><Undo2 size={14} /></button>
          <button className="btn btn-icon" title="Next version (newest branch)" data-testid="version-forward" disabled={!canFwd}
            onClick={() => ed.run(api.checkout(block.id, { direction: "forward" }))}><Redo2 size={14} /></button>
        </div>
      </div>
      <div className="mt-2 space-y-1 max-h-48 overflow-auto scroll-thin">
        {[...tree.versions].reverse().map((v) => {
          const onPath = path.includes(v.id);
          return (
            <button key={v.id} onClick={() => ed.run(api.checkout(block.id, { version_id: v.id }))}
              className={`w-full text-left rounded px-2 py-1 text-xs ${v.id === tree.head ? "bg-[var(--ui-accent-soft)]" : "hover:bg-[var(--color-surface-3)]"}`}>
              <div className="flex items-center gap-1">
                <span className={`chip ${v.author === "agent" ? "chip-agent" : ""}`}>{authorLabel(ed.cfg.ui, v.author)}</span>
                {!onPath && <GitBranch size={11} className="text-[var(--color-text-muted)]" />}
                {v.children.length > 1 && <span className="text-[10px] text-[var(--color-text-muted)]">forks {v.children.length}</span>}
                <span className="text-[var(--color-text-muted)] ml-auto">{new Date(v.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
              </div>
              <div className="truncate text-[var(--color-text-secondary)] mt-0.5">{v.note || "-"} · {blockText(block.type, v.content).slice(0, 60)}</div>
            </button>
          );
        })}
      </div>
      <div className="text-[11px] text-[var(--color-text-muted)] mt-1">Editing an older version starts a new branch; nothing is overwritten.</div>
    </div>
  );
}

function ContentEditor({ block, patch }: { block: Block; patch: (c: any, note?: string) => Promise<any> }) {
  const ed = useEditorApi();
  const c = block.content;
  const fileRef = useRef<HTMLInputElement>(null);
  const [sketching, setSketching] = useState(false);
  switch (block.type) {
    case "text":
    case "callout":
      return (
        <>
          {block.type === "callout" && (
            <>
              <span className="label">Tone</span>
              <select className="select" value={c.tone || "info"} onChange={(e) => patch({ tone: e.target.value })}>
                <option value="info">Info</option><option value="note">Note</option>
                <option value="success">Success</option><option value="warn">Warning</option>
                <option value="dark">Dark (white on black)</option>
              </select>
            </>
          )}
          <span className="label">Markdown</span>
          <MarkdownArea value={c.markdown || ""} onCommit={(v) => patch({ markdown: v })} />
          <button className="btn mt-2" onClick={() => ed.setEditing(block.id)}>Edit on the page</button>
        </>
      );
    case "heading":
      return (
        <>
          <span className="label">Text</span>
          <TextInput value={c.text || ""} onCommit={(v) => patch({ text: v })} testid="heading-text" />
          <span className="label">Level</span>
          <select className="select" value={c.level || 1} onChange={(e) => patch({ level: Number(e.target.value) })}>
            <option value={1}>Title (H1)</option><option value={2}>Section (H2)</option><option value={3}>Subsection (H3)</option>
          </select>
        </>
      );
    case "image":
      return (
        <>
          <span className="label">Source</span>
          <div className="flex gap-2">
            <TextInput value={c.src || ""} placeholder="https://… or upload" onCommit={(v) => patch({ src: v })} />
            <button className="btn btn-icon" title="Upload an image" onClick={() => fileRef.current?.click()}><Upload size={14} /></button>
            <input ref={fileRef} type="file" accept="image/*" hidden onChange={async (e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              const u = await ed.run(api.upload(ed.doc.id, f, "asset"));
              if (u) await patch({ src: u.id, alt: c.alt || f.name.replace(/\.[^.]+$/, "") }, "image uploaded");
            }} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div><span className="label">Label</span>
              <TextInput value={c.label || ""} placeholder="FIGURE 1" onCommit={(v) => patch({ label: v })} /></div>
            <div><span className="label">Title</span>
              <TextInput value={c.title || ""} onCommit={(v) => patch({ title: v })} /></div>
          </div>
          <span className="label">Source line</span>
          <TextInput value={c.source || ""} placeholder="Source: …" onCommit={(v) => patch({ source: v })} />
          <span className="label">Caption</span>
          <TextInput value={c.caption || ""} onCommit={(v) => patch({ caption: v })} />
          <span className="label">Alt text</span>
          <TextInput value={c.alt || ""} onCommit={(v) => patch({ alt: v })} />
          <span className="label">Fit (fixed height)</span>
          <select className="select" value={c.fit || "contain"} onChange={(e) => patch({ fit: e.target.value })}>
            <option value="contain">Contain</option><option value="cover">Cover (crop)</option>
          </select>
          <span className="label">Sketch</span>
          <div className="text-[11.5px] text-[var(--color-text-muted)] mb-2">
            Draw what you have in mind; {agentName(ed.cfg.ui)} can turn it into an image (or find one).
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn" data-testid="open-sketch" onClick={() => setSketching(true)}>
              <PenLine size={14} /> {c.sketch ? "Edit sketch" : "Draw a sketch"}</button>
            {c.sketch && (
              <button className="btn btn-agent" data-testid="sketch-to-image" disabled={!ed.cfg.chat_available || ed.agentRunning}
                onClick={() => ed.ask(`Turn the sketch on image block ${block.id} into a finished image${c.caption ? ` (caption: "${c.caption}")` : ""}. Look at it with view_sketch first.`)}>
                <Wand2 size={14} /> Turn into an image</button>
            )}
            {c.sketch && <button className="btn btn-ghost" onClick={() => patch({ sketch: "" }, "sketch removed")}>Remove sketch</button>}
          </div>
          {sketching && (
            <SketchPad initial={c.sketch ? withBase(`/api/uploads/${c.sketch}/file`) : undefined} onClose={() => setSketching(false)}
              onSave={async (png) => {
                const file = new File([png], `sketch-${block.id}.png`, { type: "image/png" });
                const u = await ed.run(api.upload(ed.doc.id, file, "asset"));
                if (u) await patch({ sketch: u.id }, "sketch drawn");
                setSketching(false);
              }} />
          )}
        </>
      );
    case "video":
      return (
        <>
          <span className="label">Source</span>
          <div className="flex gap-2">
            <TextInput value={c.src || ""} placeholder="https://….mp4 or upload" onCommit={(v) => patch({ src: v })} />
            <button className="btn btn-icon" title="Upload a video or GIF" onClick={() => fileRef.current?.click()}><Upload size={14} /></button>
            <input ref={fileRef} type="file" accept="video/mp4,video/webm,image/gif" hidden data-testid="video-upload" onChange={async (e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (!f) return;
              const u = await ed.run(api.upload(ed.doc.id, f, "asset"));
              if (!u) return;
              // Capture a poster frame in the browser: the PDF/PNG export shows it.
              let poster = "";
              if (f.type.startsWith("video/")) {
                const blob = await posterFrame(withBase(`/api/uploads/${u.id}/file`)).catch(() => null);
                if (blob) {
                  const p = await api.upload(ed.doc.id, new File([blob], `poster-${block.id}.jpg`, { type: "image/jpeg" }), "asset").catch(() => null);
                  poster = p?.id || "";
                }
              }
              await patch({ src: u.id, mime: f.type, poster }, "video uploaded");
            }} />
          </div>
          <span className="label">Caption</span>
          <TextInput value={c.caption || ""} onCommit={(v) => patch({ caption: v })} />
          <label className="flex items-center gap-2 mt-3">
            <input type="checkbox" checked={!!c.loop} onChange={(e) => patch({ loop: e.target.checked })} /> Loop
          </label>
          <label className="flex items-center gap-2 mt-1">
            <input type="checkbox" checked={c.muted !== false} onChange={(e) => patch({ muted: e.target.checked })} /> Muted
          </label>
          <div className="text-[11px] text-[var(--color-text-muted)] mt-2">
            PDF and image exports show the poster frame; HTML export plays the video; MP4 export is a page slideshow.
          </div>
        </>
      );
    case "table":
      return <TableEditor block={block} patch={patch} />;
    case "chart":
      return (
        <>
          <span className="label">Vega-Lite spec (JSON)</span>
          <JsonArea value={c.spec || {}} onCommit={(v) => patch({ spec: v })} />
          <span className="label">Caption</span>
          <TextInput value={c.caption || ""} onCommit={(v) => patch({ caption: v })} />
          <div className="text-[11px] text-[var(--color-text-muted)] mt-1">Tip: ask {agentName(ed.cfg.ui)} “make this a bar chart of …”.</div>
        </>
      );
    case "references":
      return (
        <>
          <span className="label">Title</span>
          <TextInput value={c.title || "References"} onCommit={(v) => patch({ title: v })} />
          <div className="text-[11px] text-[var(--color-text-muted)] mt-1">Lists every block's sources automatically, in reading order.</div>
        </>
      );
    default:
      return <div className="text-xs text-[var(--color-text-muted)]">No content to edit.</div>;
  }
}

function MarkdownArea({ value, onCommit }: { value: string; onCommit: (v: string) => void }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return <textarea className="textarea font-mono" style={{ fontSize: 12, minHeight: 120 }} value={v}
    onChange={(e) => setV(e.target.value)} onBlur={() => v !== value && onCommit(v)} />;
}

function JsonArea({ value, onCommit }: { value: any; onCommit: (v: any) => void }) {
  const initial = JSON.stringify(value, null, 2);
  const [v, setV] = useState(initial);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setV(JSON.stringify(value, null, 2)), [value]);
  return (
    <>
      <textarea className="textarea font-mono" style={{ fontSize: 11.5, minHeight: 180 }} value={v} data-testid="chart-spec"
        onChange={(e) => setV(e.target.value)}
        onBlur={() => {
          if (v === initial) return;
          try {
            onCommit(JSON.parse(v));
            setErr(null);
          } catch (e: any) {
            setErr(e.message);
          }
        }} />
      {err && <div className="text-xs text-[var(--color-error)] mt-1">{err}</div>}
    </>
  );
}

function TableEditor({ block, patch }: { block: Block; patch: (c: any) => Promise<any> }) {
  const [rows, setRows] = useState<string[][]>(block.content.rows || [[""]]);
  const dirty = JSON.stringify(rows) !== JSON.stringify(block.content.rows);
  const set = (i: number, j: number, val: string) => setRows(rows.map((r, a) => (a === i ? r.map((c, b) => (b === j ? val : c)) : r)));
  const cols = Math.max(1, ...rows.map((r) => r.length));
  return (
    <>
      <div className="overflow-auto scroll-thin">
        <table className="text-xs border-collapse">
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {Array.from({ length: cols }).map((_, j) => (
                  <td key={j} className="p-0.5">
                    <input className="input" style={{ padding: "2px 4px", minWidth: 70, fontWeight: i === 0 && block.content.header !== false ? 600 : 400 }}
                      value={r[j] ?? ""} onChange={(e) => set(i, j, e.target.value)} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-1 mt-2">
        <button className="btn" onClick={() => setRows([...rows, Array(cols).fill("")])}><Plus size={12} /> Row</button>
        <button className="btn" onClick={() => setRows(rows.map((r) => [...r, ""]))}><Plus size={12} /> Column</button>
        <button className="btn" disabled={rows.length < 2} onClick={() => setRows(rows.slice(0, -1))}>− Row</button>
        <button className="btn" disabled={cols < 2} onClick={() => setRows(rows.map((r) => r.slice(0, cols - 1)))}>− Column</button>
        <button className="btn btn-primary" disabled={!dirty} onClick={() => patch({ rows })}>Save table</button>
      </div>
      <label className="flex items-center gap-2 mt-2">
        <input type="checkbox" checked={block.content.header !== false} onChange={(e) => patch({ header: e.target.checked })} /> First row is a header
      </label>
      <span className="label">Caption</span>
      <TextInput value={block.content.caption || ""} onCommit={(v) => patch({ caption: v })} />
    </>
  );
}

function References({ block }: { block: Block }) {
  const ed = useEditorApi();
  const [refs, setRefs] = useState<Ref[]>(block.refs);
  const dirty = JSON.stringify(refs) !== JSON.stringify(block.refs);
  return (
    <div className="panel-section">
      <div className="flex items-center gap-2 mb-1">
        <div className="font-semibold">Sources</div>
        <span className="chip">{block.refs.length}</span>
      </div>
      {refs.map((r, i) => (
        <div key={i} className="border border-[var(--color-border)] rounded p-2 mb-2 space-y-1">
          <input className="input" placeholder="Title" value={r.title} onChange={(e) => setRefs(refs.map((x, k) => (k === i ? { ...x, title: e.target.value } : x)))} />
          <input className="input" placeholder="URL" value={r.url} onChange={(e) => setRefs(refs.map((x, k) => (k === i ? { ...x, url: e.target.value } : x)))} />
          <div className="flex gap-1">
            <input className="input" placeholder="Note" value={r.note} onChange={(e) => setRefs(refs.map((x, k) => (k === i ? { ...x, note: e.target.value } : x)))} />
            <button className="btn btn-icon btn-danger" onClick={() => setRefs(refs.filter((_, k) => k !== i))}><Trash2 size={13} /></button>
          </div>
        </div>
      ))}
      <div className="flex gap-2">
        <button className="btn" onClick={() => setRefs([...refs, { title: "", url: "", note: "" }])}><Plus size={12} /> Add source</button>
        {dirty && <button className="btn btn-primary" onClick={() => ed.run(api.patchBlock(block.id, { refs: refs.filter((r) => r.title || r.url), note: "sources updated" }))}>Save sources</button>}
        <button className="btn ml-auto" disabled={!ed.cfg.chat_available || ed.agentRunning}
          onClick={() => ed.ask(`Find and cite sources for block ${block.id}.`)}><Bot size={14} /> Find sources</button>
      </div>
    </div>
  );
}

/** First frame (at 0.1s) of a video, as a JPEG blob, captured in the author's browser. */
function posterFrame(url: string): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const v = document.createElement("video");
    v.muted = true;
    v.preload = "auto";
    v.crossOrigin = "anonymous";
    v.src = url;
    const fail = () => reject(new Error("could not read the video"));
    v.onerror = fail;
    v.onloadeddata = () => { v.currentTime = Math.min(0.1, (v.duration || 1) / 2); };
    v.onseeked = () => {
      const c = document.createElement("canvas");
      c.width = v.videoWidth;
      c.height = v.videoHeight;
      c.getContext("2d")!.drawImage(v, 0, 0);
      c.toBlob((b) => (b ? resolve(b) : fail()), "image/jpeg", 0.85);
    };
    setTimeout(fail, 15000);
  });
}

/** Typographic styles per block type (mirrors BLOCK_VARIANTS in store.py). */
const VARIANTS: Record<string, string[]> = {
  text: ["lead", "pullquote", "kicker", "byline", "small", "caption", "footer", "bio"],
  heading: ["display", "section", "rule", "label"],
  image: ["figure-box"],
  chart: ["figure-box"],
  table: ["figure-box"],
  callout: ["figure-box", "dark"],
  references: ["hanging"],
};
