import { useEffect, useRef, useState } from "react";

import { chartSpec } from "./chartSpec";

/** Charts that are still rendering; the print route waits for zero before exporting. */
export const pendingCharts = { count: 0, listeners: new Set<() => void>() };

function settle(delta: number) {
  pendingCharts.count += delta;
  pendingCharts.listeners.forEach((l) => l());
}

export const DEFAULT_CHART_HEIGHT = 220;

/** A Vega-Lite chart rendered as SVG (crisp in PDF). Fixed height so layout is deterministic. */
export function Chart({ spec, width, height }: { spec: any; width: number; height: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const key = JSON.stringify(spec) + width + "x" + height;

  useEffect(() => {
    let cancelled = false;
    let view: any = null;
    if (!spec || !Object.keys(spec).length) {
      setError("Empty chart: add a Vega-Lite spec.");
      return;
    }
    settle(1);
    (async () => {
      try {
        const { default: embed } = await import("vega-embed");
        if (cancelled || !ref.current) return;
        const res = await embed(ref.current, chartSpec(spec, width, height), { renderer: "svg", actions: false });
        view = res.view;
        setError(null);
      } catch (e: any) {
        if (!cancelled) setError(String(e?.message || e));
      } finally {
        settle(-1);
      }
    })();
    return () => {
      cancelled = true;
      view?.finalize?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return (
    <div style={{ width, height, overflow: "hidden" }}>
      {error ? <div className="cd-chart-error">Chart error: {error}</div> : <div ref={ref} />}
    </div>
  );
}
