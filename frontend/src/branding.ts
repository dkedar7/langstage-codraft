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

function block(selector: string, colors: Record<string, string> | undefined): string {
  const decls = Object.entries(colors || {})
    .filter(([, v]) => typeof v === "string" && SAFE.test(v))
    .map(([k, v]) => `${cssVar(k)}:${v};`).join("");
  return decls ? `${selector}{${decls}}` : "";
}

export function brandCss(ui: UiConfig): string {
  const parts = [block(":root", ui.colors), block(".dark", ui.colors_dark)];
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

export function feature(ui: UiConfig | undefined, name: keyof NonNullable<UiConfig["features"]>): boolean {
  return ui?.features?.[name] ?? true;
}
