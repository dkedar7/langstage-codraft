import { RotateCcw, Upload as UploadIcon, Wand2, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { agentName, feature } from "../branding";
import { familyOf } from "../fonts";
import { effectiveTheme, pageDesign } from "../render/theme";
import type { FontInfo, HeaderFooter, PageDesign, Settings, Theme } from "../types";
import { NumberInput, TextInput } from "./BlockPanel";
import { useEditorApi } from "./context";

const GENERIC: Record<string, string> = {
  sans: "system-ui, -apple-system, 'Segoe UI', Arial, sans-serif",
  serif: "Georgia, 'Times New Roman', serif",
  mono: "Consolas, 'Courier New', monospace",
};

function useDesign() {
  const ed = useEditorApi();
  const { doc, cfg } = ed;
  const s = doc.settings;
  const theme = effectiveTheme(cfg.themes, s);
  const base = cfg.themes[s.theme] || theme;
  const set = (patch: Partial<Settings> | Record<string, any>) => ed.run(api.patchDoc(doc.id, { settings: patch }));
  return { ed, doc, cfg, s, theme, base, set };
}

/** The Design tab: cover and back page, fonts and colours, header and footer, saved looks
 *  and matching an uploaded document. Everything applies to this document only. */
export function DesignPanel() {
  const { ed, doc, cfg, s, theme, base, set } = useDesign();
  const styleOn = feature(cfg.ui, "style_editor");
  const hasCover = doc.blocks.some((b) => b.layout.role === "cover");
  const hasBack = doc.blocks.some((b) => b.layout.role === "back");

  return (
    <div className="overflow-auto scroll-thin h-full" data-testid="design-panel">
      <Section title="Cover page">
        {hasCover ? (
          <>
            <label className="flex items-center gap-2 text-[12.5px]">
              <input type="checkbox" data-testid="cover-enabled" checked={s.cover?.enabled !== false}
                onChange={(e) => set({ cover: { enabled: e.target.checked } })} />
              Give the cover blocks their own page
            </label>
            <Hint>Off: the title and subtitle open page 1 and the summary starts right below them.</Hint>
            {s.cover?.enabled !== false && <PageDesignFields kind="cover" />}
          </>
        ) : (
          <>
            <Hint>This document has no cover. Add one, or mark any block as a cover block (Block → Role).</Hint>
            <button className="btn mt-2" data-testid="add-cover" onClick={async () => {
              const first = doc.blocks[0];
              await ed.run(api.addBlock(doc.id, { type: "heading", content: { text: doc.title, level: 1 }, after: null,
                layout: { role: "cover" } }));
              await set({ cover: { enabled: true } });
              if (first) ed.toast("Added a cover page with the title. Add more to it with “+ Add to cover”.", "info");
            }}>Add a cover page</button>
          </>
        )}
      </Section>

      {hasBack && (
        <Section title="Back page">
          <PageDesignFields kind="back" />
        </Section>
      )}

      {styleOn && (
        <Section title="Fonts and colours" right={Object.keys(s.style || {}).length ? (
          <button className="btn btn-ghost text-[11.5px]" data-testid="style-reset" onClick={() => set({ style: null })}>
            <RotateCcw size={12} /> Reset to {base.label}</button>) : null}>
          <Hint>On top of the {base.label} theme, for this document only.</Hint>
          <div className="grid grid-cols-2 gap-x-2">
            <FontField label="Body" k="font_body" theme={theme} />
            <FontField label="Headings" k="font_heading" theme={theme} />
            <FontField label="Titles and kickers" k="font_display" theme={theme} />
            <FontField label="Labels and captions" k="font_label" theme={theme} />
            <Field label="Body size (px)" k="font_size">
              <NumberInput value={theme.font_size} min={8} max={28} step={0.5} onCommit={(v) => set({ style: { font_size: v } })} />
            </Field>
            <Field label="Line spacing" k="line_height">
              <NumberInput value={theme.line_height} min={1} max={2.4} step={0.05} onCommit={(v) => set({ style: { line_height: v } })} />
            </Field>
            <Field label="Heading scale" k="heading_scale">
              <NumberInput value={theme.heading_scale} min={1} max={1.8} step={0.01} onCommit={(v) => set({ style: { heading_scale: v } })} />
            </Field>
            <Field label="Heading weight" k="heading_weight">
              <select className="select" value={theme.heading_weight} onChange={(e) => set({ style: { heading_weight: Number(e.target.value) } })}>
                {[300, 400, 500, 600, 700, 800].map((w) => <option key={w} value={w}>{w}</option>)}
              </select>
            </Field>
            <Field label="Heading case" k="heading_case">
              <select className="select" value={theme.heading_case || "none"} onChange={(e) => set({ style: { heading_case: e.target.value } })}>
                <option value="none">As written</option><option value="uppercase">UPPERCASE</option><option value="capitalize">Capitalized</option>
              </select>
            </Field>
            <Field label="Text alignment" k="text_align">
              <select className="select" value={theme.text_align || "left"} onChange={(e) => set({ style: { text_align: e.target.value } })}>
                <option value="left">Left</option><option value="justify">Justified</option>
              </select>
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-x-2 mt-1">
            <ColorStyle label="Text" k="color_text" theme={theme} />
            <ColorStyle label="Headings" k="color_heading" theme={theme} fallback={theme.color_accent} />
            <ColorStyle label="Accent" k="color_accent" theme={theme} />
            <ColorStyle label="Muted text" k="color_muted" theme={theme} />
            <ColorStyle label="Rules" k="color_rule" theme={theme} />
            <ColorStyle label="Callout tint" k="color_callout" theme={theme} />
            <ColorStyle label="Figure tint" k="color_figure" theme={theme} fallback={theme.color_callout} />
            <ColorStyle label="Paper" k="color_page" theme={theme} />
          </div>
          <SaveTheme />
        </Section>
      )}

      <Section title="Header and footer">
        <Hint>On body pages, in the margins. {"{page}"}, {"{pages}"}, {"{title}"} and {"{year}"} fill in per page.</Hint>
        <HeaderFooterFields part="header" />
        <HeaderFooterFields part="footer" />
        {!s.footer?.left && !s.footer?.center && !s.footer?.right && (
          <button className="btn mt-2" data-testid="add-page-numbers" onClick={() => set({ footer: { right: "{page}" } })}>
            Add page numbers</button>
        )}
      </Section>

      <MatchStyle />
    </div>
  );
}

function Field({ label, k, children }: { label: string; k: keyof Theme; children: ReactNode }) {
  const { s, set } = useDesign();
  const own = (s.style as any)?.[k] != null;
  return (
    <div>
      <span className="label flex items-center gap-1">
        {label}
        {own && (
          <button className="ml-auto text-[var(--color-text-muted)] hover:text-[var(--color-text)]" title="Back to the theme's value"
            onClick={() => set({ style: { [k]: null } })}><X size={11} /></button>
        )}
      </span>
      {children}
    </div>
  );
}

function FontField({ label, k, theme }: { label: string; k: keyof Theme; theme: Theme }) {
  const { cfg, set } = useDesign();
  const fonts = cfg.fonts || [];
  const current = familyOf((theme as any)[k] || (k === "font_display" || k === "font_label" ? theme.font_heading : ""));
  const known = fonts.some((f) => f.family === current);
  const [other, setOther] = useState(false);
  const pick = (fam: string) => {
    const info: FontInfo | undefined = fonts.find((f) => f.family === fam);
    set({ style: { [k]: `'${fam}', ${GENERIC[info?.category || "sans"]}` } });
  };
  return (
    <Field label={label} k={k}>
      {other ? (
        <TextInput value="" placeholder="Any Google Font, e.g. Nunito" onCommit={(v) => { setOther(false); if (v.trim()) pick(v.trim()); }} />
      ) : (
        <select className="select" data-testid={`font-${k}`} value={known ? current : "__current"} style={{ fontFamily: (theme as any)[k] }}
          onChange={(e) => (e.target.value === "__other" ? setOther(true) : e.target.value !== "__current" && pick(e.target.value))}>
          {!known && <option value="__current">{current || "Theme font"}</option>}
          <optgroup label="Included">
            {fonts.filter((f) => f.bundled).map((f) => <option key={f.family} value={f.family}>{f.family}</option>)}
          </optgroup>
          {cfg.web_fonts !== false && (
            <optgroup label="Google Fonts (loaded on demand)">
              {fonts.filter((f) => !f.bundled).map((f) => <option key={f.family} value={f.family}>{f.family}</option>)}
              <option value="__other">Other Google Font…</option>
            </optgroup>
          )}
        </select>
      )}
    </Field>
  );
}

function ColorStyle({ label, k, theme, fallback }: { label: string; k: keyof Theme; theme: Theme; fallback?: string }) {
  const { set } = useDesign();
  const value = ((theme as any)[k] as string) || fallback || "";
  return (
    <Field label={label} k={k}>
      <ColorInput value={value} testid={`color-${k}`} onCommit={(v) => set({ style: { [k]: v } })} />
    </Field>
  );
}

function PageDesignFields({ kind }: { kind: "cover" | "back" }) {
  const { ed, doc, s, theme, set } = useDesign();
  const own: PageDesign = (kind === "cover" ? s.cover : s.back) || {};
  const look = pageDesign(kind, s, theme);
  const fileRef = useRef<HTMLInputElement>(null);
  const put = (patch: PageDesign) => set({ [kind]: patch });
  return (
    <div className="mt-1">
      <span className="label">Place the {kind} blocks</span>
      <div className="flex gap-1" data-testid={`${kind}-align`}>
        {(["top", "center", "bottom"] as const).map((a) => (
          <button key={a} className={`btn flex-1 justify-center ${look.align === a ? "btn-primary" : ""}`} onClick={() => put({ align: a })}>
            {a === "center" ? "Middle" : a[0].toUpperCase() + a.slice(1)}</button>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-x-2">
        <div>
          <span className="label flex items-center">Background colour
            {own.background && <button className="ml-auto text-[var(--color-text-muted)]" title="No colour" onClick={() => put({ background: null })}><X size={11} /></button>}
          </span>
          <ColorInput value={look.background || ""} placeholder="none" testid={`${kind}-background`} onCommit={(v) => put({ background: v })} />
        </div>
        <div>
          <span className="label">Text</span>
          <select className="select" value={own.text || "auto"} onChange={(e) => put({ text: e.target.value as any })}>
            <option value="auto">Automatic</option><option value="light">Light (on dark)</option><option value="dark">Dark</option>
          </select>
        </div>
      </div>
      <span className="label">Background image</span>
      <div className="flex gap-1 items-center">
        <button className="btn" onClick={() => fileRef.current?.click()}><UploadIcon size={13} /> {look.image ? "Replace" : "Upload"}</button>
        {own.image && <button className="btn btn-ghost" onClick={() => put({ image: null })}>Remove</button>}
        {look.image && <span className="text-[11px] text-[var(--color-text-muted)] truncate">fills the page, cropped to fit</span>}
        <input ref={fileRef} type="file" accept="image/*" hidden onChange={async (e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (!f) return;
          const u = await ed.run(api.upload(doc.id, f, "asset"));
          if (u) await put({ image: u.id });
        }} />
      </div>
      <Hint>For art or a logo at an exact spot, select a {kind} block, then Block → Pin to a page position.</Hint>
    </div>
  );
}

function HeaderFooterFields({ part }: { part: "header" | "footer" }) {
  const { s, set } = useDesign();
  const h: HeaderFooter = s[part] || { left: "", center: "", right: "", first_page: true, rule: false };
  return (
    <div className="mt-2">
      <span className="label">{part === "header" ? "Header" : "Footer"}: left, centre, right</span>
      <div className="grid grid-cols-3 gap-1">
        {(["left", "center", "right"] as const).map((slot) => (
          <TextInput key={slot} value={h[slot] || ""} testid={`${part}-${slot}`}
            placeholder={part === "footer" && slot === "right" ? "{page}" : ""}
            onCommit={(v) => set({ [part]: { [slot]: v } })} />
        ))}
      </div>
      <div className="flex gap-4 mt-1 text-[12px]">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={h.first_page !== false} onChange={(e) => set({ [part]: { first_page: e.target.checked } })} />
          On the first page</label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={!!h.rule} onChange={(e) => set({ [part]: { rule: e.target.checked } })} /> Rule</label>
      </div>
    </div>
  );
}

function SaveTheme() {
  const { ed, doc, cfg, s, set } = useDesign();
  const [name, setName] = useState("");
  const current = cfg.themes[s.theme];
  return (
    <div className="mt-3 pt-2 border-t border-[var(--color-border)]">
      <span className="label">Save this look (fonts, colours, cover) as a theme for other documents</span>
      <div className="flex gap-1">
        <input className="input" placeholder="Theme name" value={name} data-testid="theme-name" onChange={(e) => setName(e.target.value)} />
        <button className="btn" data-testid="theme-save" disabled={!name.trim()} onClick={async () => {
          const r = await ed.run(api.saveTheme({ label: name.trim(), doc_id: doc.id }));
          if (!r) return;
          await ed.reloadConfig?.();
          await set({ theme: r.name, style: null });
          setName("");
          ed.toast(`Saved as “${name.trim()}”. It's in every document's theme list.`, "info");
        }}>Save</button>
      </div>
      {current?.saved && (
        <button className="btn btn-ghost mt-1 text-[11.5px]" onClick={async () => {
          const keep = effectiveTheme(cfg.themes, s);
          const own = Object.fromEntries(Object.entries(keep).filter(([k]) => k.startsWith("font_") || k.startsWith("color_")
            || ["font_size", "line_height", "heading_scale", "heading_weight", "heading_case", "text_align"].includes(k)));
          await set({ theme: cfg.default_theme in cfg.themes ? cfg.default_theme : "classic", style: own });
          const r = await ed.run(api.deleteTheme(s.theme));
          if (r) await ed.reloadConfig?.();
        }}>Delete the saved theme “{current.label}” (this document keeps its look)</button>
      )}
    </div>
  );
}

function MatchStyle() {
  const { ed, doc, cfg } = useDesign();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [changes, setChanges] = useState<string[] | null>(null);
  const styled = doc.uploads.filter((u) => u.purpose === "style");
  const apply = async (id: string, name: string) => {
    const r = await ed.run(api.applyStyle(id));
    if (!r) return;
    setChanges(r.changes);
    ed.toast(r.changes.length ? `Matched ${name}.` : `Nothing readable in ${name}.`, "info");
  };
  return (
    <Section title="Match a document's look">
      <Hint>Upload a PDF, Word file or page image. Codraft reads its page setup, fonts, colours, cover and running
        header/footer and applies them here; {agentName(cfg.ui)} can then match the rest by eye.</Hint>
      <button className="btn mt-2" disabled={busy} data-testid="match-upload" onClick={() => fileRef.current?.click()}>
        <Wand2 size={13} /> {busy ? "Reading…" : "Upload a document to match"}</button>
      <input ref={fileRef} type="file" hidden accept=".pdf,.docx,image/png,image/jpeg" onChange={async (e) => {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (!f) return;
        setBusy(true);
        try {
          const u = await ed.run(api.upload(doc.id, f, "style"));
          if (u) await apply(u.id, u.filename);
        } finally {
          setBusy(false);
        }
      }} />
      {changes && changes.length > 0 && (
        <ul className="mt-2 text-[12px] list-disc pl-4 space-y-0.5" data-testid="match-changes">
          {changes.map((c) => <li key={c}>{c}</li>)}
        </ul>
      )}
      {styled.map((u) => (
        <div key={u.id} className="flex items-center gap-2 mt-2 text-[12px]">
          <span className="truncate flex-1">{u.filename}</span>
          <button className="btn btn-ghost" onClick={() => apply(u.id, u.filename)}>Apply again</button>
          {cfg.chat_available && (
            <button className="btn btn-agent" onClick={() => ed.ask(`Match the look of ${u.filename} (${u.id}) more closely: `
              + "compare view_upload with view_pages, then refine the cover, heading styles, callouts and figures. Keep my text.")}>
              Ask {agentName(cfg.ui)}</button>
          )}
        </div>
      ))}
    </Section>
  );
}

function Section({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <div className="panel-section">
      <div className="flex items-center"><div className="font-semibold">{title}</div><div className="ml-auto">{right}</div></div>
      {children}
    </div>
  );
}

function Hint({ children }: { children: ReactNode }) {
  return <div className="text-[11.5px] text-[var(--color-text-muted)] mt-0.5">{children}</div>;
}

/** A colour swatch plus its hex value. */
export function ColorInput({ value, onCommit, placeholder, testid }: {
  value: string; onCommit: (v: string) => void; placeholder?: string; testid?: string;
}) {
  const hex = /^#[0-9a-f]{6}$/i.test(value) ? value : /^#[0-9a-f]{3}$/i.test(value)
    ? "#" + value.slice(1).split("").map((c) => c + c).join("") : "#ffffff";
  const [live, setLive] = useState(hex);
  useEffect(() => setLive(hex), [hex]);
  const timer = useRef<number | undefined>(undefined);
  return (
    <div className="flex items-center gap-1">
      <input type="color" value={live} className="w-8 h-7 p-0 border border-[var(--color-border)] rounded cursor-pointer"
        aria-label="Pick a colour" onChange={(e) => {
          const v = e.target.value;
          setLive(v);
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => onCommit(v), 350); // dragging the picker: one save at the end
        }} />
      <TextInput value={value} placeholder={placeholder} testid={testid}
        onCommit={(v) => { const t = v.trim(); if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(t)) onCommit(t.toLowerCase()); }} />
    </div>
  );
}
