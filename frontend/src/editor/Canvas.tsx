import type React from "react";
import { ArrowDownUp, GripVertical, MessageSquare, Pin, Sparkles, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { api } from "../api";
import type { LayoutResult, PageLayout, Placed } from "../layout/paginate";
import { BlockView } from "../render/BlockView";
import { Fragment, Page } from "../render/Page";
import { themeVars } from "../render/theme";
import { langCode, pinHeight, useDocLayout } from "../render/useDocLayout";
import { pageNumbers, usesPageToken } from "../layout/numbering";
import type { Block, Suggestion } from "../types";
import { useEditorApi } from "./context";
import { SuggestionDiff } from "./Diff";
import { TextEditor } from "./TextEditor";

/** Blocks plus pending insert suggestions as "virtual" ghost blocks, in reading order. */
function withVirtual(blocks: Block[], suggestions: Suggestion[]): Block[] {
  const inserts = suggestions.filter((s) => s.kind === "insert" && s.status === "pending");
  if (!inserts.length) return blocks;
  const out = [...blocks];
  for (const s of inserts) {
    const p = s.payload;
    const vb: Block = {
      id: `virtual-${s.id}`, doc_id: s.doc_id, type: p.type || "text", ord: 0,
      layout: { span: p.layout?.span || "column", height: null, role: "flow", pin: null, repeat: null },
      content: p.content || {}, refs: p.refs || [], head: "", n_versions: 0, author: "agent", updated_at: 0,
      virtual: { suggestionId: s.id },
    };
    if (p.after === null) {
      const firstFlow = out.findIndex((b) => b.layout.role === "flow");
      out.splice(firstFlow < 0 ? out.length : firstFlow, 0, vb);
    } else {
      const idx = out.findIndex((b) => b.id === p.after);
      const lastFlow = out.map((b) => b.layout.role).lastIndexOf("flow");
      out.splice(idx >= 0 ? idx + 1 : lastFlow + 1, 0, vb);
    }
  }
  return out;
}

interface DragState {
  id: string;
  kind: "move" | "pin" | "width" | "height";
  startX: number;
  startY: number;
  startW: number;
  startH: number;
  x: number;
  y: number;
  label: string;
}

interface DropTarget {
  page: number;
  y: number;
  x: number;
  w: number;
  after: string | null;
  pin?: { page: number; top: number; column: number };
}

export function Canvas() {
  const ed = useEditorApi();
  const { doc, cfg } = ed;
  const theme = cfg.themes[doc.settings.theme];
  const blocks = useMemo(() => withVirtual(doc.blocks, doc.suggestions), [doc.blocks, doc.suggestions]);
  const { layout, geo, ctx, measureNode, measured } = useDocLayout(blocks, doc.settings, theme, true);
  const byId = useMemo(() => Object.fromEntries(blocks.map((b) => [b.id, b])), [blocks]);
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const [drag, setDrag] = useState<DragState | null>(null);
  const [drop, setDrop] = useState<DropTarget | null>(null);
  const [openSugg, setOpenSugg] = useState<string | null>(null);

  const pendingByBlock = useMemo(() => {
    const m: Record<string, Suggestion[]> = {};
    for (const s of doc.suggestions) if (s.status === "pending" && s.block_id) (m[s.block_id] ||= []).push(s);
    return m;
  }, [doc.suggestions]);
  const peersByBlock = useMemo(() => {
    const m: Record<string, typeof ed.peers> = {};
    for (const p of ed.peers) if (p.block_id) (m[p.block_id] ||= []).push(p);
    return m;
  }, [ed.peers]);
  const lockOverride = useRef<string | null>(null);
  /** Start editing unless someone else is; a second attempt edits anyway (soft lock). */
  const startEditing = (id: string) => {
    const other = (peersByBlock[id] || []).find((p) => p.editing);
    if (other && lockOverride.current !== id) {
      lockOverride.current = id;
      ed.toast(`${other.name} is editing this block. Double-click again to edit anyway (both versions are kept).`);
      return;
    }
    lockOverride.current = null;
    ed.setEditing(id);
  };
  const commentsByBlock = useMemo(() => {
    const m: Record<string, number> = {};
    for (const c of doc.comments) if (c.status === "open" && c.block_id) m[c.block_id] = (m[c.block_id] || 0) + 1;
    return m;
  }, [doc.comments]);

  // Tell the server which page each block landed on (for the agent's outline).
  useEffect(() => {
    const t = setTimeout(() => {
      api.reportLayout(doc.id, { blocks: layout.blockPages, _pages: layout.pages.length }).catch(() => {});
    }, 600);
    return () => clearTimeout(t);
  }, [doc.id, layout]);

  // ------------------------------------------------------------ drag & resize
  const flowOrder = useMemo(() => doc.blocks.filter((b) => b.layout.role === "flow" && !b.layout.pin).map((b) => b.id),
    [doc.blocks]);

  const computeDrop = (clientX: number, clientY: number, d: DragState): DropTarget | null => {
    for (const p of layout.pages) {
      const el = pageRefs.current[p.number];
      if (!el) continue;
      const r = el.getBoundingClientRect();
      if (clientX < r.left || clientX > r.right || clientY < r.top || clientY > r.bottom) continue;
      const lx = (clientX - r.left) / ed.zoom - geo.margin.left;
      const ly = (clientY - r.top) / ed.zoom - geo.margin.top;
      if (d.kind === "pin") {
        const col = Math.max(0, Math.min(geo.columns - 1, Math.floor(lx / (layout.colW + geo.colGap))));
        const top = Math.max(0, Math.round((ly - 8) / 4) * 4);
        const b = byId[d.id];
        const span = b?.layout.pin?.span || 1;
        return { page: p.number, x: col * (layout.colW + geo.colGap), y: top, w: span * layout.colW + (span - 1) * geo.colGap,
          after: null, pin: { page: bodyIndex(layout, p.number), top, column: col } };
      }
      if (p.kind !== "body") return null;
      const items = p.items.filter((it) => !it.pinned && it.id !== d.id && !byId[it.id]?.virtual);
      // Items in the column under the pointer (or full-width ones).
      const inCol = items.filter((it) => lx >= it.x - 4 && lx <= it.x + it.w + 4);
      const pool = inCol.length ? inCol : items;
      if (!pool.length) {
        // empty page: drop at the end of the document flow
        const last = flowOrder.filter((x) => x !== d.id).slice(-1)[0] ?? null;
        return { page: p.number, x: 0, y: 0, w: layout.contentW, after: last };
      }
      let best = pool[0];
      let bestDist = Infinity;
      for (const it of pool) {
        const dist = Math.min(Math.abs(ly - it.y), Math.abs(ly - (it.y + it.h)), ly > it.y && ly < it.y + it.h ? 0 : Infinity);
        if (dist < bestDist) { bestDist = dist; best = it; }
      }
      const before = ly < best.y + best.h / 2;
      const order = flowOrder.filter((x) => x !== d.id);
      const idx = order.indexOf(best.id);
      const after = before ? (idx > 0 ? order[idx - 1] : null) : best.id;
      return { page: p.number, x: best.x, w: best.w, y: before ? best.y - geo.blockGap / 2 - 1 : best.y + best.h + geo.blockGap / 2 - 1, after };
    }
    return null;
  };

  const startDrag = (e: RPointerEvent, b: Block, item: Placed, kind: DragState["kind"]) => {
    e.preventDefault();
    e.stopPropagation();
    ed.select(b.id);
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const text = b.type === "heading" ? b.content.text : b.type === "text" ? b.content.markdown : b.type;
    setDrag({ id: b.id, kind, startX: e.clientX, startY: e.clientY, startW: item.w, startH: item.h,
      x: e.clientX, y: e.clientY, label: String(text || b.type).slice(0, 60) });
  };

  const onPointerMove = (e: RPointerEvent) => {
    if (!drag) return;
    const d = { ...drag, x: e.clientX, y: e.clientY };
    setDrag(d);
    if (d.kind === "move" || d.kind === "pin") setDrop(computeDrop(e.clientX, e.clientY, d));
  };

  const onPointerUp = async (e: RPointerEvent) => {
    if (!drag) return;
    const d = drag;
    const target = drop;
    setDrag(null);
    setDrop(null);
    const moved = Math.abs(e.clientX - d.startX) + Math.abs(e.clientY - d.startY) > 4;
    if (!moved) return;
    const b = byId[d.id];
    if (!b) return;
    if (d.kind === "move" && target) {
      const cur = flowOrder.indexOf(d.id);
      const prev = cur > 0 ? flowOrder[cur - 1] : null;
      if (target.after === prev || target.after === d.id) return;
      const ok = await ed.run(api.moveBlock(d.id, target.after));
      if (ok) ed.toast("Moved. Ask the agent to rewrite the neighbouring blocks for continuity?", "info");
      if (ok) continuityPrompt.current = d.id;
    } else if (d.kind === "pin" && target?.pin) {
      await ed.run(api.patchBlock(d.id, { layout: { pin: { ...(b.layout.pin || { span: 1, height: null }), ...target.pin } } }));
    } else if (d.kind === "width") {
      const newW = d.startW + (e.clientX - d.startX) / ed.zoom;
      if (b.layout.pin) {
        const span = Math.max(1, Math.min(geo.columns - b.layout.pin.column,
          Math.round((newW + geo.colGap) / (layout.colW + geo.colGap))));
        if (span !== b.layout.pin.span) await ed.run(api.patchBlock(d.id, { layout: { pin: { ...b.layout.pin, span } } }));
      } else {
        const full = newW > layout.colW * 1.25 && geo.columns > 1;
        const span = full ? "full" : "column";
        if (span !== b.layout.span) await ed.run(api.patchBlock(d.id, { layout: { span } }));
      }
    } else if (d.kind === "height") {
      const h = Math.max(24, Math.round((d.startH + (e.clientY - d.startY) / ed.zoom) / 8) * 8);
      if (b.layout.pin) await ed.run(api.patchBlock(d.id, { layout: { pin: { ...b.layout.pin, height: h } } }));
      else await ed.run(api.patchBlock(d.id, { layout: { height: h } }));
    }
  };

  const continuityPrompt = useRef<string | null>(null);
  const [continuity, setContinuity] = useState<string | null>(null);
  useEffect(() => {
    if (continuityPrompt.current) {
      setContinuity(continuityPrompt.current);
      continuityPrompt.current = null;
    }
  }, [doc.rev]);

  // ------------------------------------------------------------------ render
  const firstFragment = (id: string) => {
    for (const p of layout.pages) for (const it of p.items) if (it.id === id && !it.repeated) return { p, it };
    return null;
  };
  const editingAt = ed.editing ? firstFragment(ed.editing) : null;

  const numbering = pageNumbers(layout.pages, {
    start: doc.settings.page_number_start ?? 1,
    numberCoverPages: !!doc.settings.number_cover_pages,
    unnumbered: doc.settings.unnumbered_pages || [],
  });
  const pctx = { ...ctx, title: doc.title, pageCount: numbering.last };
  const renderItem = (it: Placed, page: PageLayout) => {
    const b = byId[it.id];
    if (!b) return null;
    // Page-number blocks hide on unnumbered pages (same rule as the export).
    if (b.layout.pin && numbering.map[page.number] == null && usesPageToken(b.content)) return null;
    const sel = ed.selected === b.id;
    const pend = pendingByBlock[b.id] || [];
    const nComments = commentsByBlock[b.id] || 0;
    const isVirtual = !!b.virtual;
    const meas = measured[b.id];
    const fixed = pinHeight(b) || b.layout.height;
    const contentOverflow = !!fixed && !!meas && meas.natural > fixed + 1 && ["text", "callout"].includes(b.type);
    const cls = ["ed-item", sel && "selected", pend.length && "has-suggestion", isVirtual && "virtual",
      (it.overflow || contentOverflow) && "overflow", it.pinned && "pinned"].filter(Boolean).join(" ");
    const showChrome = !it.continued && !it.repeated;
    return (
      <div key={`${it.id}-${it.offset}-${page.number}`} className={cls} data-testid={`block-${b.type}`}
        data-block-id={b.id}
        style={{ left: it.x, top: it.y, width: it.w, height: it.h, opacity: drag?.id === b.id && drag.kind === "move" ? 0.35 : 1 }}
        onPointerDown={(e) => {
          if (isVirtual) return;
          if ((e.target as HTMLElement).closest(".ed-handle,.ed-resize-r,.ed-resize-b,.ed-badges,.ed-sugg-bar")) return;
          if (b.layout.pin && sel && e.altKey === false && e.detail === 1) {
            // dragging a selected pinned block moves its pin
            startDrag(e, b, it, "pin");
          }
        }}
        onClick={(e) => {
          e.stopPropagation();
          if (isVirtual) { setOpenSugg(b.virtual!.suggestionId); return; }
          if (sel && (b.type === "text" || b.type === "callout" || b.type === "heading")) startEditing(b.id);
          else ed.select(b.id);
        }}
        onDoubleClick={(e) => {
          e.stopPropagation();
          if (!isVirtual && ["text", "callout", "heading"].includes(b.type)) startEditing(b.id);
          else if (!isVirtual) ed.setTab("block");
        }}>
        <Fragment item={it}>
          <BlockView block={b} width={it.w} ctx={pctx} fixedHeight={fixed} pageNo={numbering.map[page.number] ?? undefined} />
        </Fragment>
        {it.continued && <div className="ed-cont top" />}
        {showChrome && (peersByBlock[b.id] || []).slice(0, 1).map((p) => (
          <div key={p.client_id} style={{ ["--peer" as string]: p.color } as React.CSSProperties}>
            <div className="ed-peer" />
            <div className="ed-peer-tag" data-testid="peer-tag">{p.name}{p.editing ? " · editing" : ""}</div>
          </div>
        ))}
        <div className="ed-outline" />
        {showChrome && !isVirtual && (
          <>
            <div className="ed-handle" title={b.layout.pin ? "Drag to move the pinned block" : "Drag to move"}
              onPointerDown={(e) => startDrag(e, b, it, b.layout.pin ? "pin" : "move")} data-testid="drag-handle">
              <GripVertical size={14} />
            </div>
            <div className="ed-resize-r" title="Drag to change width (snaps to columns)"
              onPointerDown={(e) => startDrag(e, b, it, "width")} />
            <div className="ed-resize-b" title="Drag to set a fixed height; double-click for auto"
              onPointerDown={(e) => startDrag(e, b, it, "height")}
              onDoubleClick={(e) => {
                e.stopPropagation();
                ed.run(api.patchBlock(b.id, { layout: b.layout.pin ? { pin: { ...b.layout.pin, height: null } } : { height: null } }));
              }} />
          </>
        )}
        {showChrome && (
          <div className="ed-badges">
            {isVirtual && <span className="ed-badge agent"><Sparkles size={10} /> Suggested block</span>}
            {pend.map((s) => (
              <span key={s.id} className="ed-badge agent" data-testid="suggestion-badge"
                onClick={(e) => { e.stopPropagation(); setOpenSugg(openSugg === s.id ? null : s.id); }}>
                <Sparkles size={10} /> {s.kind === "edit" ? "Review" : s.kind === "delete" ? "Delete?" : "Move?"}
              </span>
            ))}
            {nComments > 0 && (
              <span className="ed-badge comment" onClick={(e) => {
                e.stopPropagation(); ed.select(b.id); ed.setCommentFilter(b.id); ed.setTab("comments");
              }}><MessageSquare size={10} /> {nComments}</span>
            )}
            {it.pinned && <span className="ed-badge" title={b.layout.repeat ? `Repeats: ${JSON.stringify(b.layout.repeat)}` : "Pinned"}><Pin size={10} /></span>}
            {(it.overflow || contentOverflow) && (
              <span className="ed-badge warn" title={contentOverflow ? "Content is taller than the fixed height" : "Taller than the page"}>
                <TriangleAlert size={10} /> overflow</span>
            )}
          </div>
        )}
        {isVirtual && showChrome && <SuggestionBar suggestionId={b.virtual!.suggestionId} />}
        {!isVirtual && pend.map((s) => openSugg === s.id && (
          <SuggestionPopover key={s.id} s={s} block={b} onClose={() => setOpenSugg(null)} />
        ))}
        {isVirtual && openSugg === b.virtual!.suggestionId && (
          <SuggestionPopover s={doc.suggestions.find((x) => x.id === b.virtual!.suggestionId)!} block={undefined}
            onClose={() => setOpenSugg(null)} />
        )}
      </div>
    );
  };

  return (
    <div className="relative" onPointerMove={onPointerMove} onPointerUp={onPointerUp}
      onClick={() => { ed.select(null); setOpenSugg(null); }}>
      <div style={{ zoom: ed.zoom }}>
        <div className="cd-doc cd-pages" style={themeVars(theme)} lang={langCode(doc.settings)}>
          {layout.pages.map((p) => (
            <div key={p.number} ref={(el) => { pageRefs.current[p.number] = el; }} style={{ position: "relative" }}>
              <div className="ed-page-label">
                {p.kind === "cover" ? "Cover" : p.kind === "back" ? "Back page" : `Page ${p.number}`}
                {numbering.map[p.number] != null && numbering.map[p.number] !== p.number ? ` · printed as ${numbering.map[p.number]}` : ""}
                {numbering.map[p.number] == null && p.kind === "body" ? " · unnumbered" : ""}
                {doc.settings.mode === "pageless" ? " (pageless)" : ""}
              </div>
              <Page page={p} geo={geo} renderItem={renderItem}>
                {drop && drop.page === p.number && (
                  <div className="ed-drop" style={{ left: geo.margin.left + drop.x, top: geo.margin.top + drop.y,
                    width: drop.w, height: drop.pin ? 24 : 3, background: drop.pin ? "rgba(26,77,143,.12)" : undefined,
                    border: drop.pin ? "1px dashed var(--ui-accent)" : undefined }} />
                )}
                {editingAt && editingAt.p.number === p.number && byId[ed.editing!] && (
                  <InlineEditor block={byId[ed.editing!]} item={editingAt.it} left={geo.margin.left} top={geo.margin.top} />
                )}
              </Page>
            </div>
          ))}
        </div>
      </div>
      {drag && (drag.kind === "move" || drag.kind === "pin") && (
        <div className="ed-ghost" style={{ left: drag.x + 12, top: drag.y + 8 }}>
          <ArrowDownUp size={11} style={{ display: "inline", marginRight: 4 }} />{drag.label}
        </div>
      )}
      {continuity && (
        <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-50 bg-[var(--color-card)] border border-[var(--color-border)] shadow-lg rounded-lg px-4 py-3 flex items-center gap-3"
          onClick={(e) => e.stopPropagation()}>
          <span>Block moved. Rewrite the affected blocks for continuity?</span>
          <button className="btn btn-agent" onClick={async () => {
            const id = continuity;
            setContinuity(null);
            await ed.run(api.addComment(doc.id, { body: "This block was moved. Rewrite it and its neighbouring blocks so the text reads continuously.", block_id: id }));
          }}>Queue comment</button>
          <button className="btn btn-agent" onClick={async () => {
            const id = continuity;
            setContinuity(null);
            const c = await ed.run(api.addComment(doc.id, { body: "This block was moved. Rewrite it and its neighbouring blocks so the text reads continuously.", block_id: id }));
            if (c) ed.ask(`Address comment ${c.id} on block ${id}.`);
          }}>Ask now</button>
          <button className="btn btn-ghost" onClick={() => setContinuity(null)}>Dismiss</button>
        </div>
      )}
      {measureNode}
    </div>
  );
}

function bodyIndex(layout: LayoutResult, pageNumber: number): number {
  const body = layout.pages.filter((p) => p.kind === "body");
  const i = body.findIndex((p) => p.number === pageNumber);
  return i < 0 ? 1 : i + 1;
}

function InlineEditor({ block, item, left, top }: { block: Block; item: Placed; left: number; top: number }) {
  const ed = useEditorApi();
  const base = useRef(block.head).current; // the version this edit started from
  const style = { left: left + item.x - 6, top: top + item.y - 6, width: item.w + 12, padding: 6, minHeight: item.h + 12 };
  if (block.type === "heading") {
    return (
      <div className="ed-editor" style={style} onClick={(e) => e.stopPropagation()}>
        <HeadingInput block={block} base={base} />
      </div>
    );
  }
  const isCallout = block.type === "callout";
  return (
    <div className={`ed-editor ${isCallout ? "cd-callout cd-tone-" + (block.content.tone || "info") : ""}`} style={style}
      onClick={(e) => e.stopPropagation()}>
      <TextEditor markdown={block.content.markdown || ""} placeholder={isCallout ? "Callout…" : "Write…"}
        onCancel={() => {
          ed.setEditing(null);
          if (!block.content.markdown && block.n_versions <= 1) ed.run(api.deleteBlock(block.id));
        }}
        onCommit={async (md) => {
          ed.setEditing(null);
          if (md !== (block.content.markdown || "")) {
            await ed.run(api.patchBlock(block.id, { content: { markdown: md }, base, author: ed.me.name }));
          }
        }} />
    </div>
  );
}

function HeadingInput({ block, base }: { block: Block; base: string }) {
  const ed = useEditorApi();
  const [v, setV] = useState(block.content.text || "");
  const L = block.content.level || 1;
  const done = async (save: boolean) => {
    ed.setEditing(null);
    if (save && v !== block.content.text) {
      await ed.run(api.patchBlock(block.id, { content: { text: v }, base, author: ed.me.name }));
    }
  };
  return (
    <input autoFocus value={v} data-testid="heading-input" placeholder="Heading"
      className={`cd-h cd-h${L}`} style={{ width: "100%", border: 0, outline: "none", background: "transparent" }}
      onChange={(e) => setV(e.target.value)} onBlur={() => done(true)}
      onKeyDown={(e) => { if (e.key === "Enter") done(true); if (e.key === "Escape") done(false); }} />
  );
}

function SuggestionBar({ suggestionId }: { suggestionId: string }) {
  const ed = useEditorApi();
  return (
    <div className="ed-sugg-bar" onClick={(e) => e.stopPropagation()}>
      <button className="btn btn-agent" data-testid="accept-insert" onClick={() => ed.run(api.accept(suggestionId))}>Accept</button>
      <button className="btn" onClick={() => ed.run(api.reject(suggestionId))}>Reject</button>
    </div>
  );
}

function SuggestionPopover({ s, block, onClose }: { s: Suggestion; block: Block | undefined; onClose: () => void }) {
  const ed = useEditorApi();
  if (!s) return null;
  return (
    <div className="absolute z-40 bg-[var(--color-card)] rounded-lg shadow-xl border border-[var(--color-border)] p-3"
      style={{ top: "100%", left: 0, marginTop: 8, width: 380, fontFamily: "Inter, sans-serif", fontSize: 12.5, color: "#1c1c22", lineHeight: 1.45 }}
      onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
      <div className="flex items-center gap-2 mb-2">
        <span className="chip chip-agent"><Sparkles size={10} /> {s.kind}</span>
        <span className="text-[var(--color-text-muted)] truncate">{s.note}</span>
      </div>
      <div className="max-h-64 overflow-auto scroll-thin border border-[var(--color-border)] rounded p-2 mb-2 bg-[var(--color-surface-2)]">
        {s.kind === "edit" ? <SuggestionDiff block={block} content={s.payload.content || {}} />
          : s.kind === "delete" ? <span>Delete this block.</span>
          : s.kind === "move" ? <span>Move this block {s.payload.after ? "after another block" : "to the start"}.</span>
          : <span>Insert a new {s.payload.type} block.</span>}
        {s.payload.refs?.length ? (
          <div className="mt-2 text-[var(--color-text-muted)]">Sources: {s.payload.refs.map((r: any) => r.title).join("; ")}</div>
        ) : null}
      </div>
      <div className="flex gap-2">
        <button className="btn btn-agent" data-testid="accept-suggestion"
          onClick={async () => { onClose(); await ed.run(api.accept(s.id), "Accepted"); }}>Accept</button>
        <button className="btn" data-testid="reject-suggestion"
          onClick={async () => { onClose(); await ed.run(api.reject(s.id), "Rejected"); }}>Reject</button>
        <button className="btn btn-ghost ml-auto" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}
