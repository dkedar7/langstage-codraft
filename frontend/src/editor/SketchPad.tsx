import type React from "react";
import { Eraser, Pencil, RotateCcw, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

const COLORS = ["#111827", "#2563eb", "#dc2626", "#059669", "#d97706"];
const W = 960;
const H = 600;

/** Draw a rough sketch on an image block. The agent can see it (`view_sketch`) and
 *  turn it into a real image with an image tool, or find one. Saved as a PNG upload. */
export function SketchPad({ initial, onSave, onClose }: {
  initial?: string;
  onSave: (png: Blob) => Promise<void> | void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [color, setColor] = useState(COLORS[0]);
  const [size, setSize] = useState(4);
  const [erase, setErase] = useState(false);
  const [busy, setBusy] = useState(false);
  const history = useRef<ImageData[]>([]);
  const drawing = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const c = ref.current!;
    const ctx = c.getContext("2d")!;
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, W, H);
    if (initial) {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => ctx.drawImage(img, 0, 0, W, H);
      img.src = initial;
    }
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [initial, onClose]);

  const pos = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * W, y: ((e.clientY - r.top) / r.height) * H };
  };
  const ctx = () => ref.current!.getContext("2d")!;

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6" onClick={onClose}>
      <div className="card w-full max-w-4xl p-0 overflow-hidden" onClick={(e) => e.stopPropagation()} data-testid="sketch-pad">
        <div className="flex items-center gap-2 px-4 h-11 border-b border-[var(--color-border)]">
          <Pencil size={14} className="text-[var(--color-text-muted)]" />
          <span className="font-semibold text-[var(--color-text)]">Sketch</span>
          <span className="text-xs text-[var(--color-text-muted)]">a rough drawing the agent can turn into an image</span>
          <button className="btn btn-ghost btn-icon ml-auto" onClick={onClose}><X size={15} /></button>
        </div>
        <div className="p-4 bg-[var(--color-panel)]">
          <canvas ref={ref} width={W} height={H} className="sketch-canvas w-full" data-testid="sketch-canvas"
            onPointerDown={(e) => {
              (e.target as Element).setPointerCapture(e.pointerId);
              history.current.push(ctx().getImageData(0, 0, W, H));
              if (history.current.length > 30) history.current.shift();
              drawing.current = pos(e);
            }}
            onPointerMove={(e) => {
              if (!drawing.current) return;
              const p = pos(e);
              const c = ctx();
              c.strokeStyle = erase ? "#fff" : color;
              c.lineWidth = erase ? size * 5 : size;
              c.lineCap = "round";
              c.lineJoin = "round";
              c.beginPath();
              c.moveTo(drawing.current.x, drawing.current.y);
              c.lineTo(p.x, p.y);
              c.stroke();
              drawing.current = p;
            }}
            onPointerUp={() => { drawing.current = null; }} />
        </div>
        <div className="flex items-center gap-2 px-4 py-3 border-t border-[var(--color-border)]">
          {COLORS.map((c) => (
            <button key={c} title={c} onClick={() => { setColor(c); setErase(false); }}
              className="w-6 h-6 rounded-full border-2" style={{ background: c, borderColor: color === c && !erase ? "var(--color-primary)" : "transparent" }} />
          ))}
          <select className="select" style={{ width: 90 }} value={size} onChange={(e) => setSize(Number(e.target.value))}>
            <option value={2}>Fine</option><option value={4}>Medium</option><option value={8}>Thick</option>
          </select>
          <button className={`btn ${erase ? "btn-primary" : ""}`} onClick={() => setErase(!erase)}><Eraser size={14} /> Eraser</button>
          <button className="btn" onClick={() => { const h = history.current.pop(); if (h) ctx().putImageData(h, 0, 0); }}>
            <RotateCcw size={14} /> Undo</button>
          <button className="btn" onClick={() => {
            history.current.push(ctx().getImageData(0, 0, W, H));
            const c = ctx(); c.fillStyle = "#fff"; c.fillRect(0, 0, W, H);
          }}><Trash2 size={14} /> Clear</button>
          <button className="btn btn-primary ml-auto" disabled={busy} data-testid="sketch-save" onClick={() => {
            setBusy(true);
            ref.current!.toBlob(async (b) => {
              if (b) await onSave(b);
              setBusy(false);
            }, "image/png");
          }}>{busy ? "Saving…" : "Save sketch"}</button>
        </div>
      </div>
    </div>
  );
}
