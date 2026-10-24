import { describe, expect, it } from "vitest";
import { googleFontsUrl, webFamilies } from "../fonts";
import type { Settings, Theme } from "../types";
import { baseThemeName, effectiveTheme, pageDesign, themeVars } from "./theme";

const themes = {
  classic: { label: "Classic", font_body: "Georgia", color_accent: "#1a4d8f", color_text: "#111111" },
  swan: { label: "SWAN", color_accent: "#002f58", cover_background: "#002f58" },
} as unknown as Record<string, Theme>;
const settings = (over: Partial<Settings> = {}) => ({ theme: "classic", ...over }) as Settings;

describe("document themes (0.5)", () => {
  it("layers the document's own overrides on its theme", () => {
    const t = effectiveTheme(themes, settings({ style: { color_accent: "#009fe3", color_text: null as any } }));
    expect(t.color_accent).toBe("#009fe3");
    expect(t.color_text).toBe("#111111");
    expect(effectiveTheme(themes, settings({ theme: "gone" })).label).toBe("Classic");
  });

  it("exposes a heading colour only when one is set", () => {
    expect(themeVars({ ...themes.classic, color_heading: "#7a1f3d" } as Theme)).toHaveProperty("--cd-heading", "#7a1f3d");
    expect(themeVars(themes.classic)).not.toHaveProperty("--cd-heading");
  });

  it("picks light text on dark covers and takes the theme's cover when the document has none", () => {
    expect(pageDesign("cover", settings({ cover: { background: "#002f58" } }), themes.classic)).toMatchObject({ light: true, align: "center" });
    expect(pageDesign("cover", settings({ cover: { background: "#f8f4ec" } }), themes.classic).light).toBe(false);
    expect(pageDesign("cover", settings({ cover: { background: "#f8f4ec", text: "light" } }), themes.classic).light).toBe(true);
    expect(pageDesign("cover", settings({ theme: "swan" }), themes.swan)).toMatchObject({ background: "#002f58", light: true });
    expect(pageDesign("back", settings({ theme: "swan" }), themes.swan).background).toBeNull();
  });

  it("fetches only fonts that aren't bundled or installed", () => {
    expect(webFamilies({ font_body: "'Lato', sans-serif", font_heading: "'Nunito Sans', Arial", font_label: "Georgia, serif",
      font_display: "'Crimson Pro', serif" } as Partial<Theme>)).toEqual(["Nunito Sans", "Crimson Pro"]);
    expect(googleFontsUrl("Nunito Sans")).toContain("family=Nunito+Sans:ital,wght@");
    expect(googleFontsUrl("Nunito Sans", false)).toBe("https://fonts.googleapis.com/css2?family=Nunito+Sans&display=swap");
  });
});

describe("a theme that isn't offered (gh #15)", () => {
  const offered = { acme: { label: "Acme" }, academic: { label: "Academic" } } as unknown as Record<string, Theme>;
  it("falls back to the deployer's default_theme, not the first theme left", () => {
    expect(baseThemeName(offered, settings({ theme: "modern" }), "acme")).toBe("acme");
    expect(effectiveTheme(offered, settings({ theme: "modern" }), "acme").label).toBe("Acme");
    expect(baseThemeName(offered, settings({ theme: "academic" }), "acme")).toBe("academic");
    expect(baseThemeName(offered, settings({ theme: "modern" }), "gone")).toBe("acme");
  });
});
