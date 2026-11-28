import { describe, expect, it } from "vitest";
import { downloadName } from "./downloadName";

describe("the export's file name (gh #79)", () => {
  it("prefers filename* so a title in any script keeps its letters", () => {
    expect(downloadName(`attachment; filename="document.pdf"; filename*=UTF-8''%E6%97%A5%E6%9C%AC%E3%81%AE%E5%A0%B1%E5%91%8A.pdf`))
      .toBe("日本の報告.pdf");
  });
  it("falls back to the plain filename", () => {
    expect(downloadName(`attachment; filename="rapport-d-activite-2026.pdf"`)).toBe("rapport-d-activite-2026.pdf");
    expect(downloadName(`attachment; filename="brief.pdf"; filename*=UTF-8''%E0%A4%ZZ.pdf`)).toBe("brief.pdf");
  });
  it("is null without a name", () => {
    expect(downloadName("attachment")).toBeNull();
  });
});
