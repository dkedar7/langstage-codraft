import type { CSSProperties } from "react";
import type { Theme } from "../types";

export const FALLBACK_THEME: Theme = {
  label: "Classic",
  font_body: "'Source Serif 4', Georgia, serif",
  font_heading: "'Source Serif 4', Georgia, serif",
  font_mono: "'JetBrains Mono', Consolas, monospace",
  font_size: 15,
  line_height: 1.55,
  heading_scale: 1.25,
  color_text: "#1d1d1f",
  color_muted: "#5f6368",
  color_accent: "#1a4d8f",
  color_page: "#ffffff",
  color_rule: "#d6d6d6",
  color_callout: "#eef3fa",
  heading_weight: 700,
  heading_case: "none",
};

/** Theme -> CSS custom properties consumed by styles.css (.cd-doc ...). */
export function themeVars(t: Theme | undefined): CSSProperties {
  const th = { ...FALLBACK_THEME, ...(t || {}) };
  const s = th.heading_scale;
  return {
    "--cd-font-body": th.font_body,
    "--cd-font-heading": th.font_heading,
    "--cd-font-mono": th.font_mono,
    "--cd-size": `${th.font_size}px`,
    "--cd-lh": String(th.line_height),
    "--cd-h1": `${(th.font_size * s * s * s).toFixed(2)}px`,
    "--cd-h2": `${(th.font_size * s * s).toFixed(2)}px`,
    "--cd-h3": `${(th.font_size * s).toFixed(2)}px`,
    "--cd-text": th.color_text,
    "--cd-muted": th.color_muted,
    "--cd-accent": th.color_accent,
    "--cd-page": th.color_page,
    "--cd-rule": th.color_rule,
    "--cd-callout": th.color_callout,
    "--cd-hw": String(th.heading_weight),
    "--cd-hcase": th.heading_case,
  } as CSSProperties;
}
