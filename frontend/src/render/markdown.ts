import DOMPurify from "dompurify";
import { marked } from "marked";
import TurndownService from "turndown";

marked.setOptions({ gfm: true, breaks: false });

/** Markdown -> sanitized HTML (agent-written content is untrusted). */
export function mdToHtml(md: string): string {
  const html = marked.parse(md || "", { async: false }) as string;
  return DOMPurify.sanitize(html, { ADD_ATTR: ["target"] });
}

const td = new TurndownService({
  headingStyle: "atx",
  bulletListMarker: "-",
  codeBlockStyle: "fenced",
  emDelimiter: "*",
  strongDelimiter: "**",
});
td.addRule("strike", {
  filter: ["del", "s"] as any,
  replacement: (content) => `~~${content}~~`,
});

/** Editor HTML -> Markdown (the canonical stored form of text blocks). */
export function htmlToMd(html: string): string {
  return td.turndown(html || "").replace(/\n{3,}/g, "\n\n").trim();
}

export function plainText(md: string): string {
  return (md || "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[#>*_`~|-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
