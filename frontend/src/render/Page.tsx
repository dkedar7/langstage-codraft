import type { CSSProperties, ReactNode } from "react";
import type { Geometry, PageLayout, Placed } from "../layout/paginate";
import type { HeaderFooter, Settings } from "../types";
import { fillTokens, imageSrc, type RenderCtx } from "./BlockView";

export interface PageDecor {
  background?: string | null;
  image?: string | null;
  /** light text (a dark cover) */
  light?: boolean;
}

/** One physical page: a fixed-size box with its placed items at absolute positions. */
export function Page({ page, geo, renderItem, children, className = "", decor, furniture }: {
  kind?: string;
  page: PageLayout;
  geo: Geometry;
  renderItem: (item: Placed, page: PageLayout) => ReactNode;
  children?: ReactNode;
  className?: string;
  /** cover/back page background and text colour */
  decor?: PageDecor | null;
  /** the running header and footer (in the margins) */
  furniture?: ReactNode;
}) {
  const pageless = geo.mode === "pageless";
  const w = pageless ? Math.min(geo.pageW, 720 + geo.margin.left + geo.margin.right) : geo.pageW;
  const h = pageless ? page.height + geo.margin.top + geo.margin.bottom : geo.pageH;
  const style: CSSProperties = { width: w, height: h, position: "relative" };
  if (decor?.background) Object.assign(style, { background: decor.background, "--cd-page": decor.background });
  return (
    <div className={`cd-page cd-page-${page.kind}${decor?.light ? " cd-on-dark" : ""} ${className}`}
      data-page={page.number} data-kind={page.kind} style={style}>
      {decor?.image && <img className="cd-page-bg" src={imageSrc(decor.image)} alt="" draggable={false} />}
      {furniture}
      <div className="cd-page-content" style={{
        position: "absolute", left: geo.margin.left, top: geo.margin.top,
        width: w - geo.margin.left - geo.margin.right, height: h - geo.margin.top - geo.margin.bottom,
      }}>
        {page.items.map((it) => renderItem(it, page))}
      </div>
      {children}
    </div>
  );
}

/** The visible slice of a block: clipped to the item box, shifted for continued fragments. */
export function Fragment({ item, children }: { item: Placed; children: ReactNode }) {
  return (
    <div className="cd-item-clip" style={{ position: "absolute", left: 0, top: 0, width: item.w, height: item.h, overflow: "hidden" }}>
      <div style={{ marginTop: -item.offset }}>{children}</div>
    </div>
  );
}

const SLOTS = ["left", "center", "right"] as const;

/** Header and footer text for one page, or null when there is none to show. */
export function furnitureText(part: HeaderFooter | undefined, firstBody: boolean, pageNo: number | undefined,
  ctx: RenderCtx): string[] | null {
  if (!part || !SLOTS.some((k) => part[k])) return null;
  if (firstBody && part.first_page === false) return null;
  const year = String(new Date().getFullYear());
  return SLOTS.map((k) => {
    const t = part[k] || "";
    if (t.includes("{page}") && pageNo == null) return ""; // an unnumbered page
    return fillTokens(t, ctx, pageNo).replace(/\{year\}/g, year);
  });
}

/** The running header and footer of a body page, drawn in its top and bottom margins. */
export function PageFurniture({ settings, geo, page, firstBody, pageNo, ctx, onClick }: {
  settings: Settings; geo: Geometry; page: PageLayout; firstBody: boolean; pageNo?: number; ctx: RenderCtx;
  onClick?: () => void;
}) {
  if (page.kind !== "body" || geo.mode === "pageless") return null;
  const parts = (["header", "footer"] as const).map((part) => ({
    part, text: furnitureText(settings[part], firstBody, pageNo, ctx), rule: !!settings[part]?.rule,
  })).filter((p) => p.text && p.text.some(Boolean));
  if (!parts.length) return null;
  return (
    <>
      {parts.map(({ part, text, rule }) => (
        <div key={part} className={`cd-hf cd-hf-${part}${rule ? " cd-hf-rule" : ""}${onClick ? " cd-hf-click" : ""} cd-item`}
          data-type="furniture" data-testid={`page-${part}`}
          style={{ left: geo.margin.left, right: geo.margin.right, height: part === "header" ? geo.margin.top : geo.margin.bottom,
            [part === "header" ? "top" : "bottom"]: 0 }}
          title={onClick ? `Running ${part}: edit it in Design` : undefined}
          onClick={onClick ? (e) => { e.stopPropagation(); onClick(); } : undefined}>
          <div className="cd-hf-row">
            {text!.map((t, i) => <span key={i} className={`cd-hf-${SLOTS[i]}`}>{t}</span>)}
          </div>
        </div>
      ))}
    </>
  );
}
