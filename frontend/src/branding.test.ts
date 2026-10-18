import { describe, expect, it } from "vitest";
import { agentName, authorLabel, brandCss } from "./branding";

describe("brandCss", () => {
  it("derives the tokens a partial palette leaves unset", () => {
    const css = brandCss({ colors: { primary: "#002f58", surface: "#faf7f1" } });
    expect(css).toContain(":root:not(.dark){");
    expect(css).toContain("--color-surface:#faf7f1;");
    expect(css).toMatch(/--color-panel:color-mix\(in srgb, var\(--color-surface\)/);
    expect(css).toMatch(/--ui-canvas:color-mix/);
    expect(css).toMatch(/--color-accent-soft:color-mix\(in srgb, var\(--color-primary\)/);
  });

  it("keeps tokens the deployer set, and drops unsafe values", () => {
    const css = brandCss({ colors: { surface: "#fff", panel: "#eee", text: "red;}body{display:none" } });
    expect(css).toContain("--color-panel:#eee;");
    expect(css).not.toContain("display:none");
  });

  it("darkens light shades and lightens dark ones", () => {
    const css = brandCss({ colors_dark: { surface: "#101820" } });
    expect(css).toContain(".dark{");
    expect(css).toContain("#ffffff");
  });
});

describe("agent naming", () => {
  it("uses the deployer's agent name, else generic words", () => {
    expect(agentName({ agent_name: "Lighthouse" })).toBe("Lighthouse");
    expect(agentName({})).toBe("the agent");
    expect(agentName({ agent_name: "Co-author" }, false)).toBe("agent");
    expect(authorLabel({ agent_name: "Lighthouse" }, "agent")).toBe("Lighthouse");
    expect(authorLabel({ agent_name: "Lighthouse" }, "Dana Rivera")).toBe("Dana Rivera");
  });
});
