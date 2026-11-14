// What the chat panel says about the agent's tool calls: a plain label per tool, a short
// description of what a call was about (a block's words, not its id), and a one-line summary
// of a long run of calls (a turn can make more than a hundred).

/** Past-tense labels: what the agent did. */
export const TOOL_LABELS: Record<string, string> = {
  get_document: "Read the outline",
  read_blocks: "Read blocks",
  search_document: "Searched the document",
  read_upload: "Read an upload",
  list_comments: "Listed comments",
  get_block_history: "Checked a block's history",
  propose_edit: "Suggested an edit",
  propose_insert: "Suggested a new block",
  propose_delete: "Suggested a deletion",
  propose_move: "Suggested a move",
  insert_blocks: "Suggested new blocks",
  draw_figure: "Drew a figure",
  reply_to_comment: "Replied to a comment",
  set_block_layout: "Changed a block's layout",
  set_document_style: "Changed the page setup",
  set_typography: "Changed fonts and colours",
  set_cover: "Changed the cover",
  set_header_footer: "Changed the header and footer",
  reserve_area: "Reserved an area",
  match_upload_style: "Matched an upload's style",
  compile_references: "Compiled references",
  view_pages: "Looked at the pages",
  view_sketch: "Looked at a sketch",
  view_upload: "Looked at an upload",
  generate_image: "Generated an image",
  web_search: "Searched the web",
  fetch_url: "Read a web page",
};

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] || name.replace(/_/g, " ");
}

function clip(s: string, n: number): string {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n - 1).trimEnd() + "…" : t;
}

/** A block in a few words: its heading, its opening words or its caption. */
interface BlockLike { id?: string; type: string; content: Record<string, any> }

export function blockName(b: BlockLike | undefined): string {
  if (!b) return "a block that is no longer there";
  const c = b.content || {};
  const words = b.type === "heading" ? c.text
    : b.type === "text" || b.type === "callout" ? String(c.markdown || "").replace(/[#>*_`~|^[\]()-]+/g, " ")
      : c.caption || c.title || c.label || "";
  const text = clip(String(words || ""), 44);
  return text ? `${b.type} “${text}”` : `a${/^[aeiou]/.test(b.type) ? "n" : ""} ${b.type} block`;
}

export interface Lookup { blocks: BlockLike[]; uploads?: { id: string; filename: string }[] }

/** What one call was about, in words. Null when the label says it all. */
export function describeCall(name: string, args: Record<string, any> | null | undefined, look: Lookup): string | null {
  const a = args || {};
  const block = (id: unknown) => blockName(look.blocks.find((b) => b.id === id));
  if (typeof a.block_id === "string") return block(a.block_id);
  if (Array.isArray(a.block_ids) && a.block_ids.length) {
    return a.block_ids.length === 1 ? block(a.block_ids[0]) : `${a.block_ids.length} blocks`;
  }
  if (Array.isArray(a.blocks)) return `${a.blocks.length} block${a.blocks.length === 1 ? "" : "s"}`;
  if (typeof a.upload_id === "string") {
    return look.uploads?.find((u) => u.id === a.upload_id)?.filename || "an upload";
  }
  if (typeof a.query === "string") return `“${clip(a.query, 44)}”`;
  if (typeof a.url === "string") {
    try { return new URL(a.url).hostname; } catch { return clip(a.url, 44); }
  }
  if (Array.isArray(a.pages) && a.pages.length) return `page${a.pages.length === 1 ? "" : "s"} ${a.pages.join(", ")}`;
  if (name === "propose_insert" && typeof a.block_type === "string") return `a${/^[aeiou]/.test(a.block_type) ? "n" : ""} ${a.block_type} block`;
  if (name === "reply_to_comment" && typeof a.message === "string") return `“${clip(a.message, 44)}”`;
  if (name === "draw_figure" && (a.title || a.label)) return `“${clip(String(a.title || a.label), 44)}”`;
  return null;
}

export interface StepGroup { label: string; count: number; failed: number }

/** Calls grouped by what they did, most frequent first (ties keep the order they happened in). */
export function groupCalls(tools: { name: string; status: string }[]): StepGroup[] {
  const by = new Map<string, StepGroup>();
  for (const t of tools) {
    const label = toolLabel(t.name);
    const g = by.get(label) || { label, count: 0, failed: 0 };
    g.count += 1;
    if (t.status === "error") g.failed += 1;
    by.set(label, g);
  }
  return Array.from(by.values()).sort((x, y) => y.count - x.count);
}

/** One line for a long run: "38 steps: changed a block's layout ×21, looked at the pages ×6,
 *  suggested an edit ×5 and 3 more kinds · 2 failed". */
export function summarizeCalls(tools: { name: string; status: string }[], show = 3): string {
  const groups = groupCalls(tools);
  const lower = (s: string) => s[0].toLowerCase() + s.slice(1);
  const parts = groups.slice(0, show).map((g) => (g.count > 1 ? `${lower(g.label)} ×${g.count}` : lower(g.label)));
  const rest = groups.length - show;
  const failed = groups.reduce((n, g) => n + g.failed, 0);
  return `${tools.length} step${tools.length === 1 ? "" : "s"}: ${parts.join(", ")}${rest > 0 ? ` and ${rest} more kind${rest === 1 ? "" : "s"}` : ""}`
    + (failed ? ` · ${failed} failed` : "");
}

/** More calls than this in one turn are folded into a summary. */
export const FOLD_AFTER = 4;
