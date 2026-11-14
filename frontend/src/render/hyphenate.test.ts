import { describe, expect, it } from "vitest";
import { hyphenateText } from "./hyphenate";

const SHY = String.fromCharCode(0xad);   // the soft hyphen
const show = (s: string) => s.replaceAll(SHY, "|");

describe("hyphenation (soft hyphens, the same in the editor and the export)", () => {
  it("marks the break points of longer words and leaves short ones alone", () => {
    expect(show(hyphenateText("Electrification of the metropolitan fleet", "en")))
      .toBe("Electrification of the met|ro|pol|i|tan fleet");
    expect(show(hyphenateText("the electrification programme", "en"))).toBe("the elec|tri|fi|ca|tion pro|gramme");
  });

  it("never leaves one letter before a break or two after it", () => {
    for (const w of hyphenateText("ability amazing building elementary operate idea", "en").split(" ")) {
      for (const [i, part] of w.split(SHY).entries()) {
        if (w.includes(SHY) && i === 0) expect(part.length).toBeGreaterThanOrEqual(2);
      }
      if (w.includes(SHY)) expect(w.split(SHY).at(-1)!.length).toBeGreaterThanOrEqual(3);
    }
  });

  it("leaves addresses, names, capitals and words the author already hyphenated", () => {
    expect(hyphenateText("Akbari and Oppenheimer in Baltimore", "en")).not.toContain(SHY);
    // ...except where asked to (a table cell too narrow for the word): a syllable break beats any break.
    expect(show(hyphenateText("Metropolitan Building", "en", true))).toBe("Met|ro|pol|i|tan Build|ing");
    expect(hyphenateText("https://example.org/reports/electrification", "en")).not.toContain(SHY);
    expect(hyphenateText("reports@example.org", "en")).not.toContain(SHY);
    expect(hyphenateText("UNESCO ENERGY", "en")).not.toContain(SHY);
    expect(hyphenateText(`electri${SHY}fication programme`, "en")).toBe(`electri${SHY}fication programme`);
  });

  it("returns the text unchanged for a language it has no patterns for", () => {
    expect(hyphenateText("Programmierumgebung", "xx")).toBe("Programmierumgebung");
  });

  it("removing the soft hyphens gives the text back", () => {
    const text = "Infrastructure characteristics predominate in city-scale simulations (Vahmani et al. 2016).";
    expect(hyphenateText(text, "en").replaceAll(SHY, "")).toBe(text);
  });
});
