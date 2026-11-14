import DOMPurify from "dompurify";
import { marked } from "marked";
import TurndownService from "turndown";
import { hyphenateHtml } from "./hyphenate";

marked.setOptions({ gfm: true, breaks: false });

// Codraft's Markdown is GitHub-flavoured Markdown plus superscript and subscript:
//   x^2^   -> x<sup>2</sup>      H~2~O  -> H<sub>2</sub>O
// (Pandoc's syntax: no spaces inside, at most 40 characters). ~~text~~ is still strikethrough,
// and the <sup> / <sub> tags work too. There is no maths: write symbols as characters.
function inlineMark(name: "sup" | "sub", mark: string, pattern: RegExp) {
  return {
    name,
    level: "inline" as const,
    start(src: string) {
      const i = src.indexOf(mark);
      return i < 0 ? undefined : i;
    },
    tokenizer(this: any, src: string, tokens: any[]) {
      // Attached to what it follows (x^2^, H~2~O): "~5 to ~10" and "a ^ b" stay as written.
      const before = tokens?.length ? String(tokens[tokens.length - 1].raw || "").slice(-1) : "";
      const m = before.trim() ? pattern.exec(src) : null;
      if (!m) return undefined;
      return { type: name, raw: m[0], text: m[1], tokens: this.lexer.inlineTokens(m[1]) };
    },
    renderer(this: any, token: any) {
      return `<${name}>${this.parser.parseInline(token.tokens)}</${name}>`;
    },
  };
}

marked.use({
  tokenizer: {
    // Only ~~two tildes~~ strike text out. GFM also takes ~one~, which turned "~5 to~10" into
    // struck-out text and would shadow subscript.
    del(this: any, src: string) {
      const m = /^~~(?=[^\s~])([\s\S]*?[^\s~])~~(?=[^~]|$)/.exec(src);
      if (!m) return undefined;
      return { type: "del", raw: m[0], text: m[1], tokens: this.lexer.inlineTokens(m[1]) } as any;
    },
  },
  extensions: [
    inlineMark("sup", "^", /^\^([^\s^[\]]{1,40})\^/),
    inlineMark("sub", "~", /^~(?!~)([^\s~]{1,40})~(?!~)/),
  ],
});

/** Markdown -> HTML, not yet sanitized (exported for tests; use mdToHtml). */
export function renderMarkdown(md: string): string {
  return marked.parse(md || "", { async: false }) as string;
}

/** Markdown -> sanitized HTML (agent-written content is untrusted). `lang` adds soft hyphens
 *  to table cells (used only when a table is too wide for whole words) and, with `hyphenate`,
 *  to paragraphs. The editor's own copy of the text is never hyphenated. */
export function mdToHtml(md: string, opts: { lang?: string | null; hyphenate?: boolean } = {}): string {
  const html = DOMPurify.sanitize(renderMarkdown(md), { ADD_ATTR: ["target"] });
  return opts.lang ? hyphenateHtml(html, opts.lang, !!opts.hyphenate) : html;
}

// What the rich-text editor keeps when it loads a block. Anything else in a block's Markdown
// (a table, a picture, raw HTML, a fourth-level heading) would be dropped the moment the
// block was opened and closed, so those blocks are edited as Markdown instead.
const RICH_TAGS = new Set(["p", "br", "strong", "b", "em", "i", "s", "del", "code", "pre", "h1", "h2", "h3",
  "ul", "ol", "li", "blockquote", "a", "hr", "sup", "sub"]);

/** True when the rich-text editor can hold this Markdown without losing any of it. */
export function richCanHold(md: string): boolean {
  for (const m of renderMarkdown(md).matchAll(/<([a-z][a-z0-9]*)/gi)) {
    if (!RICH_TAGS.has(m[1].toLowerCase())) return false;
  }
  return true;
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
// Superscript and subscript go back to the short form when it can hold them, else to the tag.
// (Turndown escapes a leading "-", "+", "=" or "#" as if it could start a list or a heading;
// inside the marks it can't, so m^-2^ stays m^-2^.)
const short = (content: string, mark: string) => {
  const c = content.replace(/^[\\]([-+=#>])/, "$1");
  return c.length <= 40 && !/[\s^~[\]]/.test(c) ? `${mark}${c}${mark}` : null;
};
td.addRule("sup", {
  filter: ["sup"],
  replacement: (content) => (content ? short(content, "^") ?? `<sup>${content}</sup>` : ""),
});
td.addRule("sub", {
  filter: ["sub"],
  replacement: (content) => (content ? short(content, "~") ?? `<sub>${content}</sub>` : ""),
});

/** Editor HTML -> Markdown (the canonical stored form of text blocks). */
export function htmlToMd(html: string): string {
  return td.turndown(html || "").replace(/\n{3,}/g, "\n\n").trim();
}

export function plainText(md: string): string {
  return (md || "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[#>*_`~|^-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
