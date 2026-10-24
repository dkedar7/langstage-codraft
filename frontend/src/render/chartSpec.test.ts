import { describe, expect, it } from "vitest";
// What vega-embed uses to read $schema (a dependency of vega-embed).
import parseSchema from "vega-schema-url-parser";
import { chartSpec, DEFAULT_SCHEMA } from "./chartSpec";

const BAR = {
  data: { values: [{ a: "x", b: 2 }] },
  mark: "bar",
  encoding: { x: { field: "a" }, y: { field: "b", type: "quantitative" } },
};

describe("chartSpec (gh #20)", () => {
  it("drops a $schema vega-embed can't parse, so the chart renders as Vega-Lite", () => {
    const bad = "https://vega-lite-schema.vercel.app/v6.json";
    expect(() => parseSchema(bad)).toThrow();
    const full = chartSpec({ $schema: bad, ...BAR }, 400, 200);
    expect(full.$schema).toBe(DEFAULT_SCHEMA);
    expect(parseSchema(full.$schema).library).toBe("vega-lite");
    expect(full.mark).toBe("bar");
  });

  it("keeps a canonical $schema, including a full Vega spec's", () => {
    const v5 = "https://vega.github.io/schema/vega-lite/v5.json";
    expect(chartSpec({ $schema: v5, ...BAR }, 400, 200).$schema).toBe(v5);
    const vega = "https://vega.github.io/schema/vega/v6.json";
    expect(chartSpec({ $schema: vega, marks: [] }, 400, 200).$schema).toBe(vega);
  });

  it("fills the default schema, size and config without losing the block's own config", () => {
    const full = chartSpec({ ...BAR, config: { axisX: { labelAngle: 45 } } }, 50, 10);
    expect(full.$schema).toBe(DEFAULT_SCHEMA);
    expect(full.width).toBe(80);
    expect(full.height).toBe(60);
    expect(full.config.axisX.labelAngle).toBe(45);
    expect(full.config.font).toContain("Inter");
  });

  it("ignores a non-string $schema", () => {
    expect(chartSpec({ $schema: 6, ...BAR }, 400, 200).$schema).toBe(DEFAULT_SCHEMA);
  });
});
