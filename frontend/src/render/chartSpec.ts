/** The full Vega-Lite spec a chart block renders: Codraft's defaults, the block's spec, then the size. */

export const DEFAULT_SCHEMA = "https://vega.github.io/schema/vega-lite/v6.json";

// vega-embed reads the library and version out of $schema with this pattern and throws
// ("Cannot read properties of null (reading 'slice')") when it doesn't match (gh #20).
const SCHEMA_URL = /\/schema\/([\w-]+)\/([\w.\-]+)\.json$/;

const markType = (unit: any): string => (typeof unit?.mark === "string" ? unit.mark : unit?.mark?.type) || "";
const isQuant = (ch: any): boolean => !!ch && (ch.type === "quantitative" || !!ch.aggregate);

/**
 * A stacked bar or area chart: make the stack follow the legend.
 *
 * Vega-Lite stacks by the colour field's own values, whatever order the legend lists them in.
 * So a chart with `color.sort: ["Vehicles", "Chargers", …]` showed that order in the legend and
 * another in the bars, and a horizontal stack ran right to left against its legend. Unless the
 * spec sets its own `order`, the segments now follow the legend: left to right in a
 * horizontal stack, top to bottom in a vertical one.
 */
export function orderStack(unit: any): any {
  const enc = unit?.encoding;
  const color = enc?.color;
  if (!enc || !color?.field || enc.order || !["bar", "area"].includes(markType(unit))) return unit;
  const horizontal = isQuant(enc.x) && !isQuant(enc.y);
  const vertical = isQuant(enc.y) && !isQuant(enc.x);
  const measure = horizontal ? enc.x : vertical ? enc.y : null;
  if (!measure || measure.stack === null || measure.stack === false) return unit;
  const listed = Array.isArray(color.sort) ? color.sort : null;
  if (listed) {
    const expr = `indexof(${JSON.stringify(listed)}, datum[${JSON.stringify(color.field)}])`;
    return {
      ...unit,
      transform: [...(unit.transform || []), { calculate: expr, as: "__cd_order" }],
      encoding: { ...enc, order: { field: "__cd_order", type: "quantitative", sort: horizontal ? "ascending" : "descending" } },
    };
  }
  // No listed order: a vertical stack already reads top to bottom like its legend.
  if (!horizontal) return unit;
  return { ...unit, encoding: { ...enc, order: { field: color.field, type: color.type || "nominal", sort: "ascending" } } };
}

const longest = (values: any[], field: string): number =>
  values.reduce((m, row) => Math.max(m, String(row?.[field] ?? "").length), 0);

/**
 * Room for text marks that sit past the plot (a value label after a bar's end, or above it).
 * Vega-Lite fits the axes and legend into the given size but not these labels, so a label
 * near the edge was cut off. Returns extra padding in px, or null when none is needed.
 */
export function labelPadding(spec: any, width: number): { right: number; top: number } | null {
  const units: any[] = Array.isArray(spec?.layer) ? spec.layer : [spec];
  let right = 0;
  let top = 0;
  for (const unit of units) {
    if (markType(unit) !== "text") continue;
    const mark = typeof unit.mark === "object" ? unit.mark : {};
    const enc = { ...(spec.encoding || {}), ...(unit.encoding || {}) };
    const size = Number(mark.fontSize) || 11;
    const values: any[] = Array.isArray((unit.data || spec.data)?.values) ? (unit.data || spec.data).values : [];
    const chars = enc.text?.field ? longest(values, enc.text.field) : String(enc.text?.value ?? mark.text ?? "").length;
    if (isQuant(enc.x) && !isQuant(enc.y)) {
      // After the end of a horizontal bar: left-aligned text runs to the right of its point.
      const share = mark.align === "left" ? 1 : mark.align === "right" ? 0 : 0.5;
      right = Math.max(right, share * chars * size * 0.6 + Math.max(0, Number(mark.dx) || 0) + 4);
    } else if (isQuant(enc.y) && !isQuant(enc.x)) {
      top = Math.max(top, size + Math.max(0, -(Number(mark.dy) || 0)) + 4);
    }
  }
  if (!right && !top) return null;
  return { right: Math.round(Math.min(right, width * 0.4)), top: Math.round(top) };
}

export function chartSpec(spec: any, width: number, height: number): any {
  const { $schema, ...rest0 } = spec || {};
  // Charts are Vega-Lite by contract; a $schema vega-embed can't parse is dropped, not fatal.
  const schema = typeof $schema === "string" && SCHEMA_URL.test($schema.split("#")[0]) ? $schema : DEFAULT_SCHEMA;
  let rest: any = rest0;
  try {
    rest = Array.isArray(rest0.layer) ? { ...rest0, layer: rest0.layer.map(orderStack) } : orderStack(rest0);
  } catch {
    rest = rest0;   // an odd spec renders as written
  }
  const w = Math.max(80, width);
  const pad = rest.padding == null ? labelPadding(rest, w) : null;
  return {
    $schema: schema,
    autosize: { type: "fit", contains: "padding" },
    background: "transparent",
    ...rest,
    ...(pad ? { padding: { left: 5, bottom: 5, top: 5 + pad.top, right: 5 + pad.right } } : {}),
    config: { font: "Inter, system-ui, sans-serif", view: { stroke: null }, axisX: { labelAngle: 0 }, ...(rest.config || {}) },
    width: w,
    height: Math.max(60, height),
  };
}
