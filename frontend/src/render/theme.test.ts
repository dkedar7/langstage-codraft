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

describe("what the author chose beats a heading variant's look", () => {
  const vars = (s: Partial<Settings>, t = themes) => themeVars(effectiveTheme(t, settings(s))) as Record<string, string>;

  it("exposes only the heading choices the document made itself", () => {
    expect(Object.keys(vars({})).filter((k) => k.startsWith("--cd-own-"))).toEqual([]);
    const v = vars({ style: { heading_case: "none", font_heading: "'Montserrat', sans-serif" } as any });
    expect(v["--cd-own-hcase"]).toBe("none");
    expect(v["--cd-own-hfont"]).toBe("'Montserrat', sans-serif");
    expect(v).not.toHaveProperty("--cd-own-hw");
    expect(v).not.toHaveProperty("--cd-own-h2");
  });

  it("sizes section headings by level once the document sets a heading scale", () => {
    const v = vars({ style: { heading_scale: 1.2, font_size: 10 } as any });
    expect([v["--cd-own-h1"], v["--cd-own-h2"], v["--cd-own-h3"]]).toEqual(["17.28px", "14.40px", "12.00px"]);
    expect(v["--cd-own-h2"]).toBe(v["--cd-h2"]);
  });

  it("keeps those choices when the look is saved as a theme", () => {
    const saved = { ...themes, mine: { label: "Mine", heading_case: "none", saved: true, explicit: ["heading_case"] } } as unknown as Record<string, Theme>;
    expect(vars({ theme: "mine" }, saved)["--cd-own-hcase"]).toBe("none");
    expect(effectiveTheme(saved, settings({ theme: "mine", style: { heading_weight: 400 } as any })).explicit)
      .toEqual(["heading_case", "heading_weight"]);
  });
});

describe("hyphenation setting", () => {
  const hyph = (base: Partial<Theme>, style: Partial<Theme>) =>
    effectiveTheme({ t: { label: "T", ...base } } as unknown as Record<string, Theme>, settings({ theme: "t", style: style as any })).hyphens;

  it("is on for justified text unless the document or the theme turns it off", () => {
    expect(hyph({}, {})).toBe("manual");
    expect(hyph({ hyphens: "manual" }, { text_align: "justify" })).toBe("auto");
    expect(hyph({ text_align: "justify" }, {})).toBe("auto");
    expect(hyph({ text_align: "justify" }, { hyphens: "manual" })).toBe("manual");
    expect(hyph({ text_align: "justify", hyphens: "none" }, {})).toBe("none");
    expect(hyph({}, { hyphens: "auto" })).toBe("auto");
  });

  it("always hands the browser manual hyphenation: Codraft places the soft hyphens", () => {
    expect(themeVars({ ...themes.classic, hyphens: "auto" } as Theme)).toHaveProperty("--cd-hyphens", "manual");
    expect(themeVars({ ...themes.classic, hyphens: "none" } as Theme)).toHaveProperty("--cd-hyphens", "none");
  });
});
