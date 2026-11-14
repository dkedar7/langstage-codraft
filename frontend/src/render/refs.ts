/**
 * References and citations. The same rules as langstage_codraft/refs.py, so the editor, the
 * exports and the agent's tools agree on every number:
 *  - a block's `refs` are its sources; `[@key]` in Markdown cites one and prints as `[3]`;
 *  - the list is numbered in order of first appearance: where the text first cites a source,
 *    or where the first block that carries it stands;
 *  - a source's private `note` is never printed.
 */
import type { Block, Ref } from "../types";

export type NumberedRef = Ref & { n: number };

const KEY = "[A-Za-z0-9_][A-Za-z0-9_:.+-]*";
/** `[@key]` or `[@one; @two]`. */
export const CITE = new RegExp(`\\[(@${KEY}(?:\\s*[;,]\\s*@${KEY})*)\\]`, "g");
export const CITE_START = new RegExp(`^\\[(@${KEY}(?:\\s*[;,]\\s*@${KEY})*)\\]`);

/** What makes two references the same source: the URL, or the title when there is none. */
export const identity = (r: Ref): string => (r.url || r.title || "").toLowerCase();

const keysOf = (inner: string): string[] => inner.split(/[;,]/).map((k) => k.trim().replace(/^@/, ""));

/** The keys cited in a piece of Markdown, in order. */
export function citeKeys(text: string): string[] {
  const out: string[] = [];
  for (const m of (text || "").matchAll(CITE)) out.push(...keysOf(m[1]));
  return out;
}

/** The Markdown a block prints, in reading order (where citations can stand). */
export function blockTexts(b: Block): string[] {
  const c = b.content || {};
  if (b.type === "text" || b.type === "callout") return [String(c.markdown || "")];
  if (b.type === "row") {
    return (Array.isArray(c.cells) ? c.cells : []).map((cell: any) => String((cell && typeof cell === "object" ? cell.markdown : cell) || ""));
  }
  if (b.type === "table") {
    return (Array.isArray(c.rows) ? c.rows : []).flatMap((row: any) => (Array.isArray(row) ? row.map((cell) => String(cell ?? "")) : []));
  }
  return [];
}

/** Every source in the document, de-duplicated and numbered in order of first appearance. */
export function compileReferences(blocks: Block[]): NumberedRef[] {
  const byId = new Map<string, Ref>();
  const keyToId = new Map<string, string>();
  for (const b of blocks) {
    for (const r of b.refs || []) {
      const id = identity(r);
      if (!id) continue;
      if (!byId.has(id)) byId.set(id, r);
      if (r.key && !keyToId.has(r.key.toLowerCase())) keyToId.set(r.key.toLowerCase(), id);
    }
  }
  const order: string[] = [];
  const seen = new Set<string>();
  const take = (id: string | undefined) => {
    if (id && byId.has(id) && !seen.has(id)) {
      seen.add(id);
      order.push(id);
    }
  };
  for (const b of blocks) {
    for (const text of blockTexts(b)) for (const key of citeKeys(text)) take(keyToId.get(key.toLowerCase()));
    for (const r of b.refs || []) take(identity(r));
  }
  return order.map((id, i) => ({ ...byId.get(id)!, n: i + 1 }));
}

/** key (lower case) -> number, including the keys duplicate entries were stored under. */
export function citeNumbers(blocks: Block[], refs: NumberedRef[] = compileReferences(blocks)): Record<string, number> {
  const byIdent = new Map(refs.map((r) => [identity(r), r.n]));
  const out: Record<string, number> = {};
  for (const r of refs) if (r.key) out[r.key.toLowerCase()] = r.n;
  for (const b of blocks) {
    for (const r of b.refs || []) {
      const n = byIdent.get(identity(r));
      if (r.key && n != null && out[r.key.toLowerCase()] == null) out[r.key.toLowerCase()] = n;
    }
  }
  return out;
}

/** `[@key]` -> `[3]` as plain text (for places that are not Markdown). */
export function replaceCites(text: string, numbers: Record<string, number>): string {
  return (text || "").replace(CITE, (_m, inner: string) => `[${keysOf(inner).map((k) => numbers[k.toLowerCase()] ?? "?").join(", ")}]`);
}

/** The printed parts of one list entry: Authors (Year). Title. Publisher. URL. Accessed DATE. */
export function refParts(r: Ref): { text: string; url: string; accessed: string } {
  const authors = (r.authors || "").trim();
  const year = (r.year || "").trim();
  // A source known only by its address is printed as the link alone.
  const title = (r.title || "").trim() === (r.url || "").trim() ? "" : (r.title || "").trim();
  const parts: string[] = [];
  if (authors) parts.push(authors + (year ? ` (${year})` : ""));
  if (title) parts.push(title + (year && !authors ? ` (${year})` : ""));
  if (r.publisher) parts.push(r.publisher.trim());
  const text = parts.filter(Boolean).map((p) => p.replace(/\.+$/, "")).join(". ");
  const url = (r.url || "").trim();
  return { text: text ? `${text}.` : "", url, accessed: r.accessed ? `Accessed ${r.accessed.trim()}.` : "" };
}

/** The HTML id of a list entry, the target of the citation links. */
export const refAnchor = (n: number): string => `cd-ref-${n}`;

export { keysOf as citeKeysOf };
