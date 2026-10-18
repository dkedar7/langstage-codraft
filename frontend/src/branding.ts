/**
 * Deployer branding for the app chrome (never the documents: those keep their own themes).
 * The server sends `ui` in /api/config; this turns it into CSS custom properties, a font,
 * a favicon and any custom CSS, so a host app (e.g. one with a navy-and-cream palette)
 * can make Codraft look like part of itself.
 */
import type { UiConfig } from "./types";
import { urlFlag, withBase } from "./base";

// [ui.colors] key -> CSS variable. Most map onto langstage-web's --color-* tokens.
const VARS: Record<string, string> = {
  canvas: "--ui-canvas",
  header: "--ui-header",
  header_text: "--ui-header-text",
};
const cssVar = (k: string) => VARS[k] || `--color-${k.replace(/_/g, "-")}`;

const SAFE = /^[#\w\s(),.%+\-/]*$/; // colour values only: no ; { } or url()

/**
 * Tokens a deployer didn't set but whose base they did, derived so a partial palette still
 * looks like one design (set only `surface` and the panels, canvas and borders follow it,
 * instead of staying the default greys). Light shades darken the base; dark ones lighten it.
 */
function derived(colors: Record<string, string>, dark: boolean): Record<string, string> {
  const shade = (base: string, pct: number) =>
    `color-mix(in srgb, var(${base}) ${100 - pct}%, ${dark ? "#ffffff" : "#000000"} ${pct}%)`;
  const out: Record<string, string> = {};
  const want = (k: string, v: string) => { if (!(k in colors)) out[k] = v; };
  if (colors.surface) {
    want("surface_2", shade("--color-surface", 2));
    want("surface_3", shade("--color-surface", 4));
    want("panel", shade("--color-surface", 2));
    want("border", shade("--color-surface", 9));
    want("canvas", shade("--color-surface", 6));
  }
  if (colors.primary) {
    want("primary_light", `color-mix(in srgb, var(--color-primary) 82%, #ffffff 18%)`);
    want("primary_dark", `color-mix(in srgb, var(--color-primary) 80%, #000000 20%)`);
    want("accent_soft", dark ? `color-mix(in srgb, var(--color-primary) 22%, var(--color-surface) 78%)`
      : `color-mix(in srgb, var(--color-primary) 9%, var(--color-surface) 91%)`);
  }
  return out;
}

function block(selector: string, colors: Record<string, string> | undefined, dark = false): string {
  const set = Object.fromEntries(Object.entries(colors || {}).filter(([, v]) => typeof v === "string" && SAFE.test(v)));
  const decls = Object.entries({ ...set, ...derived(set, dark) })
    .filter(([, v]) => typeof v === "string" && SAFE.test(v))
    .map(([k, v]) => `${cssVar(k)}:${v};`).join("");
  return decls ? `${selector}{${decls}}` : "";
}

export function brandCss(ui: UiConfig): string {
  // Light tokens must not leak into dark mode (this sheet loads after styles.css).
  const parts = [block(":root:not(.dark)", ui.colors), block(".dark", ui.colors_dark, true)];
  if (ui.font_family && SAFE.test(ui.font_family.replace(/["']/g, ""))) {
    // The chrome only: the page canvas sets its own fonts from the document theme.
    parts.push(`html,body,#root{font-family:${ui.font_family},-apple-system,"Segoe UI",Roboto,Arial,sans-serif}`);
  }
  if (ui.custom_css) parts.push(ui.custom_css);
  return parts.filter(Boolean).join("\n");
}

export function applyBranding(ui: UiConfig | undefined) {
  if (!ui) return;
  let style = document.getElementById("codraft-brand") as HTMLStyleElement | null;
  if (!style) {
    style = document.createElement("style");
    style.id = "codraft-brand";
    document.head.appendChild(style);
  }
  style.textContent = brandCss(ui);
  if (ui.font_url && /^https:\/\//.test(ui.font_url) && !document.querySelector(`link[data-codraft-font]`)) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = ui.font_url;
    link.dataset.codraftFont = "1";
    document.head.appendChild(link);
  }
  if (ui.icon_url) {
    const icon = document.querySelector<HTMLLinkElement>("link[rel~='icon']");
    if (icon) icon.href = withBase(ui.icon_url);
  }
  document.documentElement.classList.toggle("cd-embedded", isEmbedded(ui));
}

/** Shown inside another app: no Codraft header or theme toggle; the host provides both. */
export function isEmbedded(ui: UiConfig | undefined): boolean {
  return !!ui?.embedded || urlFlag("embed");
}

/** The agent's name for prose ("the agent" unless the deployer named it). */
export function agentName(ui: UiConfig | undefined, prose = true): string {
  const n = ui?.agent_name;
  return n && n !== "Co-author" ? n : prose ? "the agent" : "agent";
}

/** A comment/version author for display: "agent" becomes the agent's name. */
export function authorLabel(ui: UiConfig | undefined, author: string): string {
  return author === "agent" ? agentName(ui, false) : author;
}

export function feature(ui: UiConfig | undefined, name: keyof NonNullable<UiConfig["features"]>): boolean {
  return ui?.features?.[name] ?? true;
}
