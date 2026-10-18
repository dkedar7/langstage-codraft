/**
 * Document fonts. The bundled families (Latin subsets) render offline in the editor and in
 * every export; the list must match langstage_codraft/fonts.py BUNDLED. Any other family a
 * document names is loaded from Google Fonts when the server allows web fonts.
 */
import "@fontsource/raleway/latin-300.css";
import "@fontsource/raleway/latin-400.css";
import "@fontsource/raleway/latin-400-italic.css";
import "@fontsource/raleway/latin-700.css";
import "@fontsource/lato/latin-300.css";
import "@fontsource/lato/latin-400.css";
import "@fontsource/lato/latin-400-italic.css";
import "@fontsource/lato/latin-700.css";
import "@fontsource/open-sans/latin-300.css";
import "@fontsource/open-sans/latin-400.css";
import "@fontsource/open-sans/latin-400-italic.css";
import "@fontsource/open-sans/latin-700.css";
import "@fontsource/montserrat/latin-300.css";
import "@fontsource/montserrat/latin-400.css";
import "@fontsource/montserrat/latin-400-italic.css";
import "@fontsource/montserrat/latin-700.css";
import "@fontsource/roboto/latin-300.css";
import "@fontsource/roboto/latin-400.css";
import "@fontsource/roboto/latin-400-italic.css";
import "@fontsource/roboto/latin-700.css";
import "@fontsource/merriweather/latin-400.css";
import "@fontsource/merriweather/latin-400-italic.css";
import "@fontsource/merriweather/latin-700.css";
import "@fontsource/lora/latin-400.css";
import "@fontsource/lora/latin-400-italic.css";
import "@fontsource/lora/latin-700.css";
import "@fontsource/playfair-display/latin-400.css";
import "@fontsource/playfair-display/latin-400-italic.css";
import "@fontsource/playfair-display/latin-700.css";
import "@fontsource/eb-garamond/latin-400.css";
import "@fontsource/eb-garamond/latin-400-italic.css";
import "@fontsource/eb-garamond/latin-700.css";
import "@fontsource/libre-baskerville/latin-400.css";
import "@fontsource/libre-baskerville/latin-400-italic.css";
import "@fontsource/libre-baskerville/latin-700.css";
import "@fontsource/carlito/latin-400.css";
import "@fontsource/carlito/latin-400-italic.css";
import "@fontsource/carlito/latin-700.css";
import "@fontsource/arimo/latin-400.css";
import "@fontsource/arimo/latin-400-italic.css";
import "@fontsource/arimo/latin-700.css";
import "@fontsource/tinos/latin-400.css";
import "@fontsource/tinos/latin-400-italic.css";
import "@fontsource/tinos/latin-700.css";
import { useEffect, useState } from "react";
import type { Theme } from "./types";

export const BUNDLED = new Set([
  "Inter", "Roboto Condensed", "Source Serif 4", "JetBrains Mono", "Raleway", "Lato", "Open Sans",
  "Montserrat", "Roboto", "Merriweather", "Lora", "Playfair Display", "EB Garamond", "Libre Baskerville",
  "Carlito", "Arimo", "Tinos",
]);

/** Installed everywhere (or generic): never fetched. */
const SYSTEM = new Set([
  "system-ui", "-apple-system", "segoe ui", "arial", "helvetica", "helvetica neue", "georgia",
  "times new roman", "times", "courier new", "consolas", "monospace", "serif", "sans-serif",
  "arial narrow", "dejavu sans", "verdana", "tahoma",
]);

const FONT_KEYS = ["font_body", "font_heading", "font_display", "font_label", "font_mono"] as const;

/** First family named in a CSS font list. */
export function familyOf(stack: string | undefined): string {
  return (stack || "").split(",")[0].trim().replace(/^['"]|['"]$/g, "");
}

/** Families a theme uses that must come from Google Fonts. */
export function webFamilies(theme: Partial<Theme> | undefined): string[] {
  const out = new Set<string>();
  for (const k of FONT_KEYS) {
    const f = familyOf((theme as any)?.[k]);
    if (f && !BUNDLED.has(f) && !SYSTEM.has(f.toLowerCase())) out.add(f);
  }
  return [...out];
}

export function googleFontsUrl(family: string, full = true): string {
  const fam = encodeURIComponent(family).replace(/%20/g, "+");
  return full
    ? `https://fonts.googleapis.com/css2?family=${fam}:ital,wght@0,300;0,400;0,700;1,400&display=swap`
    : `https://fonts.googleapis.com/css2?family=${fam}&display=swap`;
}

/** Stylesheets still loading (the print view waits for them before it reports ready). */
export const pendingFonts = { count: 0 };
const loaded = new Map<string, Promise<void>>();

/** Add a family's Google Fonts stylesheet once. Families without a 300/700/italic cut make
 *  the full request fail, so it falls back to the plain one. */
export function loadWebFont(family: string): Promise<void> {
  const known = loaded.get(family);
  if (known) return known;
  pendingFonts.count++;
  const p = new Promise<void>((resolve) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.dataset.cdFont = family;
    let tried = false;
    const done = () => {
      pendingFonts.count--;
      // Let the faces start downloading, then wait for them.
      document.fonts?.load(`16px "${family}"`).catch(() => {}).finally(() => resolve());
    };
    link.onload = done;
    link.onerror = () => {
      if (!tried) {
        tried = true;
        link.href = googleFontsUrl(family, false);
      } else done();
    };
    link.href = googleFontsUrl(family, true);
    document.head.appendChild(link);
  });
  loaded.set(family, p);
  return p;
}

/** Load the web fonts a document's theme needs; returns a counter that bumps when they arrive. */
export function useDocFonts(theme: Partial<Theme> | undefined, enabled: boolean): number {
  const [tick, setTick] = useState(0);
  const key = enabled ? webFamilies(theme).join("|") : "";
  useEffect(() => {
    if (!key) return;
    let alive = true;
    Promise.all(key.split("|").map(loadWebFont)).then(() => { if (alive) setTick((t) => t + 1); });
    return () => { alive = false; };
  }, [key]);
  return tick;
}
