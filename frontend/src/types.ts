export type BlockType =
  | "text" | "heading" | "image" | "video" | "table" | "chart" | "callout" | "divider" | "references" | "page-break" | "spacer";

export interface Pin {
  page: number;
  top: number;
  column: number;
  span: number;
  height: number | null;
  /** page-anchored pins: px from the page's top-left corner (may bleed past margins) */
  anchor?: "page";
  x?: number;
  y?: number;
  w?: number;
  h?: number;
}

export interface BlockLayout {
  span: "column" | "full";
  height: number | null;
  role: "flow" | "cover" | "back";
  pin: Pin | null;
  repeat: "all" | "odd" | "even" | number[] | null;
  variant?: string | null;
  dropcap?: boolean;
}

export interface Ref { title: string; url: string; note: string }

export interface Block {
  id: string;
  doc_id: string;
  type: BlockType;
  ord: number;
  layout: BlockLayout;
  content: Record<string, any>;
  refs: Ref[];
  head: string;
  n_versions: number;
  author: string;
  updated_at: number;
  /** client-only: a pending insert suggestion rendered as a ghost block */
  virtual?: { suggestionId: string };
}

export interface Brief {
  writing_style: string;
  objective: string;
  platform: string;
  audience: string;
  language: string;
  output_format: string;
  notes: string;
}

export interface Settings {
  mode: "paged" | "pageless";
  page_size: "letter" | "a4" | "legal" | "a5" | "slide" | "custom";
  page_w_mm?: number | null;
  page_h_mm?: number | null;
  page_number_start?: number;
  number_cover_pages?: boolean;
  unnumbered_pages?: number[];
  orientation: "portrait" | "landscape";
  margins: { top: number; right: number; bottom: number; left: number };
  columns: number;
  column_gap: number;
  block_gap: number;
  theme: string;
  min_pages: number;
  approval: "required" | "auto";
  brief: Brief;
  /** cover/back page design; null values fall back to the theme */
  cover?: PageDesign & { enabled?: boolean };
  back?: PageDesign;
  /** this document's typography and colour overrides on top of its theme */
  style?: Partial<Theme>;
  header?: HeaderFooter;
  footer?: HeaderFooter;
}

export interface PageDesign {
  align?: "top" | "center" | "bottom" | null;
  background?: string | null;
  image?: string | null;
  text?: "auto" | "light" | "dark" | null;
}

export interface HeaderFooter {
  left: string;
  center: string;
  right: string;
  first_page: boolean;
  rule: boolean;
}

export interface Comment {
  id: string;
  doc_id: string;
  block_id: string | null;
  body: string;
  author: string;
  for_agent: boolean;
  status: "open" | "resolved";
  replies: { author: string; body: string; at: number }[];
  created_at: number;
  resolved_at: number | null;
}

export interface Suggestion {
  id: string;
  doc_id: string;
  block_id: string | null;
  kind: "edit" | "insert" | "delete" | "move";
  payload: Record<string, any>;
  status: string;
  comment_id: string | null;
  note: string;
  author: string;
  created_at: number;
}

export interface Upload {
  id: string;
  doc_id: string;
  filename: string;
  purpose: "reference" | "template" | "style" | "asset";
  mime: string;
  chars: number;
  url: string;
  /** rendered first pages of a PDF (server paths) */
  pages?: string[];
  /** the look read from a "style" upload */
  style?: Record<string, any> | null;
}

export interface Doc {
  id: string;
  title: string;
  settings: Settings;
  rev: number;
  created_at: number;
  updated_at: number;
  blocks: Block[];
  comments: Comment[];
  suggestions: Suggestion[];
  uploads: Upload[];
}

export interface DocSummary { id: string; title: string; updated_at: number; n_blocks: number; mode: string }

export interface Theme {
  label: string;
  font_body: string;
  font_heading: string;
  font_mono: string;
  font_size: number;
  line_height: number;
  heading_scale: number;
  color_text: string;
  color_muted: string;
  color_accent: string;
  color_page: string;
  color_rule: string;
  color_callout: string;
  heading_weight: number;
  heading_case: string;
  font_display?: string;
  display_weight?: number;
  font_label?: string;
  text_align?: string;
  hyphens?: string;
  color_figure?: string;
  paragraph_gap?: number;
  paragraph_indent?: number;
  color_heading?: string;
  cover_background?: string;
  cover_text?: "auto" | "light" | "dark";
  cover_image?: string;
  cover_align?: "top" | "center" | "bottom";
  /** a theme an author saved from a document */
  saved?: boolean;
}

export interface FontInfo { family: string; category: "sans" | "serif" | "mono"; bundled: boolean }

export interface UiConfig {
  title?: string;
  subtitle?: string;
  agent_name?: string;
  welcome_message?: string;
  icon_url?: string;
  theme?: string;
  embedded?: boolean;
  font_family?: string;
  font_url?: string;
  colors?: Record<string, string>;
  colors_dark?: Record<string, string>;
  starter_prompts?: string[];
  features?: { chat?: boolean; mcp?: boolean; doc_list?: boolean; theme_toggle?: boolean; style_editor?: boolean };
  custom_css?: string;
}

export interface AppConfig {
  ui?: UiConfig;
  user?: { name: string; id: string } | null;
  title: string;
  version: string;
  agent: { kind: string; label: string; missing_tools: string[]; hint?: string };
  approval_default: string;
  default_page_size?: string;
  default_theme: string;
  themes: Record<string, Theme>;
  templates: { id: string; name: string; description: string; has_cover?: boolean }[];
  fonts?: FontInfo[];
  web_fonts?: boolean;
  /** new documents from a template with a cover start with it on (the host's house default) */
  cover_default?: boolean;
  chat_available: boolean;
  ui_theme?: string;
  mcp_url?: string | null;
}

export interface Version {
  id: string;
  parent_id: string | null;
  content: Record<string, any>;
  refs: Ref[];
  author: string;
  note: string;
  created_at: number;
  children: string[];
}

export interface VersionTree { block_id: string; head: string; versions: Version[]; branches: string[] }

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool" | "error";
  content: string;
  tool_calls?: { id: string; name: string; args: any }[];
  name?: string;
}
