// Hyphenation that is the same everywhere. `hyphens: auto` in CSS uses whatever dictionaries a
// browser happens to have: desktop Chrome downloads them, the headless Chromium that renders
// exports has none, so the editor broke words that the PDF didn't. Codraft finds the break
// points itself (Liang patterns, as TeX does) and marks them with soft hyphens, which every
// browser breaks at identically.
import { useEffect, useState } from "react";
import createHyphenator from "hyphen";
import enUs from "hyphen/patterns/en-us";

type Hyphenator = (text: string) => string;

const SHY = String.fromCharCode(0xad);   // the soft hyphen
const make = (patterns: unknown): Hyphenator => createHyphenator(patterns, { html: false, minWordLength: 6 });

const ready: Record<string, Hyphenator> = { en: make(enUs) };

/** Languages whose patterns load on first use (English ships in the main bundle). */
const loaders: Record<string, () => Promise<{ default: unknown }>> = {
  de: () => import("hyphen/patterns/de-1996"),
  es: () => import("hyphen/patterns/es"),
  fr: () => import("hyphen/patterns/fr"),
  it: () => import("hyphen/patterns/it"),
  nl: () => import("hyphen/patterns/nl"),
  pt: () => import("hyphen/patterns/pt"),
};

/** Pattern files still loading; the print route waits for zero before it reports ready. */
export const pendingHyphenation = { count: 0, listeners: new Set<() => void>() };
const loading: Record<string, Promise<void>> = {};

function base(lang: string | null | undefined): string {
  return (lang || "en").toLowerCase().split("-")[0];
}

/** Start loading a language's patterns if it has some and they aren't here yet. */
function load(lang: string): void {
  const l = base(lang);
  if (l in ready || l in loading || !(l in loaders)) return;
  pendingHyphenation.count += 1;
  loading[l] = loaders[l]()
    .then((m) => { ready[l] = make((m as any).default ?? m); })
    .catch(() => { delete loaders[l]; })   // offline or blocked: this language goes unhyphenated
    .finally(() => {
      pendingHyphenation.count -= 1;
      pendingHyphenation.listeners.forEach((fn) => fn());
    });
}

/** True when `lang` can be hyphenated now. Re-renders the caller when its patterns arrive. */
export function useHyphenation(lang: string): boolean {
  const l = base(lang);
  const [, setTick] = useState(0);
  useEffect(() => {
    if (ready[l]) return;
    const fn = () => setTick((t) => t + 1);
    pendingHyphenation.listeners.add(fn);
    load(l);
    return () => { pendingHyphenation.listeners.delete(fn); };
  }, [l]);
  return !!ready[l];
}

const WORD = /\p{L}{6,}/gu;
// A chunk that reads as an address or a path (example.org, a/b, name@host) is left alone.
const ADDRESS = /[/@]|\w\.\w|^www\./i;

/** One word with soft hyphens at its break points: never after the first letter alone, never
 *  before the last two letters alone. In running text a word that starts with a capital is
 *  left whole: it is usually a name (Ak-bari, Balti-more), and patterns are least reliable
 *  there. `names` hyphenates those too (table cells, where the other choice is worse). */
function hyphenateWord(word: string, h: Hyphenator, names: boolean): string {
  if (word === word.toUpperCase() || (!names && word[0] !== word[0].toLowerCase())) return word;
  const parts = h(word).split(SHY);
  if (parts.length < 2) return word;
  let out = parts[0];
  let seen = parts[0].length;
  for (let i = 1; i < parts.length; i++) {
    const rest = word.length - seen;
    out += (seen >= 2 && rest >= 3 ? SHY : "") + parts[i];
    seen += parts[i].length;
  }
  return out;
}

/** Text with soft hyphens in its longer words. Text that already has soft hyphens (the author
 *  placed them) and languages without patterns come back unchanged. */
export function hyphenateText(text: string, lang?: string | null, names = false): string {
  const h = ready[base(lang)];
  if (!h || !text || text.includes(SHY)) return text;
  if (base(lang) === "de") names = true;   // German capitalizes every noun, compounds included
  return text.split(/(\s+)/).map((chunk) => (
    chunk.length < 6 || ADDRESS.test(chunk) ? chunk : chunk.replace(WORD, (w) => hyphenateWord(w, h, names))
  )).join("");
}

const SKIP = new Set(["CODE", "PRE", "A", "SUP", "SUB", "KBD", "SAMP"]);

/** Add soft hyphens to rendered Markdown: always inside table cells (they only take effect
 *  when a table is too wide to keep its words whole), and in paragraphs when `prose` is set. */
export function hyphenateHtml(html: string, lang: string | null | undefined, prose: boolean, cells = false): string {
  if (!ready[base(lang)] || typeof document === "undefined") return html;
  if (!prose && !cells && !html.includes("<table")) return html;
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  const walker = document.createTreeWalker(tpl.content, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  while (walker.nextNode()) nodes.push(walker.currentNode as Text);
  for (const node of nodes) {
    let wanted = false;
    let cell = cells;   // the whole fragment is one table cell
    let skip = false;
    for (let el = node.parentElement; el; el = el.parentElement) {
      if (SKIP.has(el.tagName)) skip = true;
      if (el.tagName === "TD" || el.tagName === "TH") cell = true;
      if (prose && el.tagName === "P") wanted = true;
    }
    if ((wanted || cell) && !skip && node.data.length >= 6) node.data = hyphenateText(node.data, lang, cell);
  }
  return tpl.innerHTML;
}
