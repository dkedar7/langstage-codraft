import type { CSSProperties } from "react";
import type { PageDesign, Settings, Theme } from "../types";

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

/** The theme a document is drawn with: its own if offered, else the deployer's
 *  default_theme, else classic, else the first one offered (the server's themes.offered_theme). */
export function baseThemeName(themes: Record<string, Theme>, s: Settings, fallback?: string): string | undefined {
  return [s.theme, fallback, "classic"].find((n) => n && themes[n]) ?? Object.keys(themes)[0];
}

/** Hyphenation for a document: its own choice, else on when the body is justified (wide word
 *  gaps otherwise), else the theme's. A theme that says "none" keeps it off. */
export function hyphenation(base: Partial<Theme>, own: Partial<Theme>): string {
  if (own.hyphens) return own.hyphens;
  const align = own.text_align || base.text_align || "left";
  if (align === "justify" && base.hyphens !== "none") return "auto";
  return base.hyphens || "manual";
}

/** The document's theme with its own overrides (settings.style) on top. `explicit` lists the
 *  style keys the author chose (in this document, or in the look a saved theme was made from):
 *  the font, case, weight and size of the section and rule heading variants are defaults that
 *  those choices beat. */
export function effectiveTheme(themes: Record<string, Theme>, s: Settings, fallback?: string): Theme {
  const name = baseThemeName(themes, s, fallback);
  const base = (name && themes[name]) || FALLBACK_THEME;
  const own = Object.fromEntries(Object.entries(s.style || {}).filter(([, v]) => v != null && v !== "")) as Partial<Theme>;
  const explicit = Array.from(new Set([...(base.explicit || []), ...Object.keys(own)]));
  return { ...FALLBACK_THEME, ...base, ...own, hyphens: hyphenation(base, own), explicit } as Theme;
}

export function hexLuminance(c: string): number | null {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec((c || "").trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].split("").map((x) => x + x).join("") : m[1];
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** A cover or back page's look: the document's choice, else the theme's. */
export function pageDesign(kind: "cover" | "back", s: Settings, t: Theme): Required<Pick<PageDesign, "align">> & {
  background: string | null; image: string | null; light: boolean;
} {
  const own = (kind === "cover" ? s.cover : s.back) || {};
  const background = own.background || (kind === "cover" ? t.cover_background : null) || null;
  const image = own.image || (kind === "cover" ? t.cover_image : null) || null;
  const align = own.align || (kind === "cover" ? t.cover_align : null) || "center";
  const text = own.text || (kind === "cover" ? t.cover_text : null) || "auto";
  const lum = background ? hexLuminance(background) : null;
  const light = text === "light" || (text === "auto" && ((lum != null && lum < 0.35) || (!background && !!image)));
  return { align, background, image, light };
}

/** Theme -> CSS custom properties consumed by styles.css (.cd-doc ...). */
export function themeVars(t: Theme | undefined): CSSProperties {
  const th = { ...FALLBACK_THEME, ...(t || {}) };
  const s = th.heading_scale;
  const chose = new Set(th.explicit || []);
  const sizes = [th.font_size * s * s * s, th.font_size * s * s, th.font_size * s].map((v) => `${v.toFixed(2)}px`);
  // What the author chose for headings beats the look of the section and rule heading variants
  // (styles.css reads these with the variant's own value as the fallback).
  const chosen: Record<string, string> = {};
  if (chose.has("heading_case")) chosen["--cd-own-hcase"] = th.heading_case;
  if (chose.has("font_heading")) chosen["--cd-own-hfont"] = th.font_heading;
  if (chose.has("heading_weight")) chosen["--cd-own-hw"] = String(th.heading_weight);
  if (chose.has("heading_scale")) sizes.forEach((v, i) => { chosen[`--cd-own-h${i + 1}`] = v; });
  return {
    "--cd-font-body": th.font_body,
    "--cd-font-heading": th.font_heading,
    "--cd-font-mono": th.font_mono,
    "--cd-size": `${th.font_size}px`,
    "--cd-lh": String(th.line_height),
    "--cd-h1": sizes[0],
    "--cd-h2": sizes[1],
    "--cd-h3": sizes[2],
    "--cd-text": th.color_text,
    "--cd-muted": th.color_muted,
    "--cd-accent": th.color_accent,
    "--cd-page": th.color_page,
    "--cd-rule": th.color_rule,
    "--cd-callout": th.color_callout,
    "--cd-hw": String(th.heading_weight),
    "--cd-hcase": th.heading_case,
    "--cd-font-display": th.font_display || th.font_heading,
    "--cd-display-weight": String(th.display_weight ?? 300),
    "--cd-font-label": th.font_label || "'Roboto Condensed', 'Arial Narrow', sans-serif",
    "--cd-align": th.text_align || "left",
    // "auto" breaks words at the soft hyphens Codraft puts in itself (render/hyphenate.ts), so
    // the editor and the export break the same words whatever dictionaries a browser has.
    "--cd-hyphens": th.hyphens === "none" ? "none" : "manual",
    "--cd-figure": th.color_figure || th.color_callout,
    "--cd-par-gap": th.paragraph_gap != null ? th.paragraph_gap + "em" : ".7em",
    "--cd-par-indent": th.paragraph_indent ? th.paragraph_indent + "em" : "0",
    ...(th.color_heading ? { "--cd-heading": th.color_heading } : {}),
    ...chosen,
  } as CSSProperties;
}
