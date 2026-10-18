import type { ReactNode } from "react";
import type { Geometry, PageLayout, Placed } from "../layout/paginate";

/** One physical page: a fixed-size box with its placed items at absolute positions. */
export function Page({ page, geo, renderItem, children, className = "" }: {
  kind?: string;
  page: PageLayout;
  geo: Geometry;
  renderItem: (item: Placed, page: PageLayout) => ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  const pageless = geo.mode === "pageless";
  const w = pageless ? Math.min(geo.pageW, 720 + geo.margin.left + geo.margin.right) : geo.pageW;
  const h = pageless ? page.height + geo.margin.top + geo.margin.bottom : geo.pageH;
  return (
    <div className={`cd-page cd-page-${page.kind} ${className}`} data-page={page.number} data-kind={page.kind}
      style={{ width: w, height: h, position: "relative" }}>
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
