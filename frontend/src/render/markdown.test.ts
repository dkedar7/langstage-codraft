import { describe, expect, it } from "vitest";
import { htmlToMd, renderMarkdown, richCanHold } from "./markdown";

const html = (md: string) => renderMarkdown(md).trim();

describe("Markdown dialect: GFM plus superscript and subscript", () => {
  it("renders ^superscript^ and ~subscript~", () => {
    expect(html("W m^-2^ day^-1^")).toBe("<p>W m<sup>-2</sup> day<sup>-1</sup></p>");
    expect(html("H~2~O and CO~2~")).toBe("<p>H<sub>2</sub>O and CO<sub>2</sub></p>");
    expect(html("the 1^st^ of **May**")).toBe("<p>the 1<sup>st</sup> of <strong>May</strong></p>");
  });

  it("keeps ~~strikethrough~~, and the tags still work", () => {
    expect(html("~~old~~ new")).toBe("<p><del>old</del> new</p>");
    expect(html("m<sup>2</sup>")).toBe("<p>m<sup>2</sup></p>");
  });

  it("leaves a lone caret or tilde, and anything with a space inside, as written", () => {
    expect(html("about ~5 to ~10 vans")).toBe("<p>about ~5 to ~10 vans</p>");
    expect(html("2^10 is 1024")).toBe("<p>2^10 is 1024</p>");
    expect(html("a ^ b ^ c")).toBe("<p>a ^ b ^ c</p>");
    expect(html("costs ~$5m to~$7m, or ~$6m~ish")).toBe("<p>costs ~$5m to~$7m, or ~$6m~ish</p>");
    expect(html("a ~single tilde~ pair")).toBe("<p>a ~single tilde~ pair</p>");
    expect(html("`x^2^` in code")).toBe("<p><code>x^2^</code> in code</p>");
  });

  it("honours the alignment row of a table", () => {
    const t = html("| Item | Cost |\n|:--|--:|\n| Vans | $1.2m |");
    expect(t).toContain('<th align="left">Item</th>');
    expect(t).toContain('<td align="right">$1.2m</td>');
  });
});

describe("which blocks the rich-text editor may open", () => {
  it("holds ordinary prose, lists, links, superscripts", () => {
    expect(richCanHold("Some **bold** text with a [link](https://example.org) and m^2^.\n\n- one\n- two\n\n> quoted")).toBe(true);
    expect(richCanHold("")).toBe(true);
  });

  it("leaves a table, a picture, raw HTML or a deep heading to the Markdown editor", () => {
    expect(richCanHold("| a | b |\n|---|---|\n| 1 | 2 |")).toBe(false);
    expect(richCanHold("![chart](upl_abc123)")).toBe(false);
    expect(richCanHold('<div style="color:red">hi</div>')).toBe(false);
    expect(richCanHold("#### Deep heading")).toBe(false);
  });
});

describe("editor HTML back to Markdown", () => {
  // turndown needs a DOM; the browser tests cover the round trip. Here: the module loads.
  it("exports the converter", () => {
    expect(typeof htmlToMd).toBe("function");
  });
});
