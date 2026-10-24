/** The full Vega-Lite spec a chart block renders: Codraft's defaults, the block's spec, then the size. */

export const DEFAULT_SCHEMA = "https://vega.github.io/schema/vega-lite/v6.json";

// vega-embed reads the library and version out of $schema with this pattern and throws
// ("Cannot read properties of null (reading 'slice')") when it doesn't match (gh #20).
const SCHEMA_URL = /\/schema\/([\w-]+)\/([\w.\-]+)\.json$/;

export function chartSpec(spec: any, width: number, height: number): any {
  const { $schema, ...rest } = spec || {};
  // Charts are Vega-Lite by contract; a $schema vega-embed can't parse is dropped, not fatal.
  const schema = typeof $schema === "string" && SCHEMA_URL.test($schema.split("#")[0]) ? $schema : DEFAULT_SCHEMA;
  return {
    $schema: schema,
    autosize: { type: "fit", contains: "padding" },
    background: "transparent",
    ...rest,
    config: { font: "Inter, system-ui, sans-serif", view: { stroke: null }, axisX: { labelAngle: 0 }, ...(rest.config || {}) },
    width: Math.max(80, width),
    height: Math.max(60, height),
  };
}
