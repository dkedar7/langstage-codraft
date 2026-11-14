import type React from "react";
import { Placeholder } from "@tiptap/extensions";
import { EditorContent, Mark, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import {
  Bold, Code, Heading2, Heading3, Italic, Link2, List, ListOrdered, Quote, Strikethrough, Subscript, Superscript,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { htmlToMd, mdToHtml, richCanHold } from "../render/markdown";

/** Superscript and subscript (x^2^, H~2~O in the Markdown), so editing a block keeps them. */
const Sup = Mark.create({ name: "superscript", excludes: "subscript",
  parseHTML: () => [{ tag: "sup" }], renderHTML: () => ["sup", 0] });
const Sub = Mark.create({ name: "subscript", excludes: "superscript",
  parseHTML: () => [{ tag: "sub" }], renderHTML: () => ["sub", 0] });

interface EditorProps {
  markdown: string;
  onCommit: (md: string) => void;
  onCancel: () => void;
  placeholder?: string;
}

/** Editing for text and callout blocks. Markdown in, Markdown out. A block with something
 *  the rich-text editor can't hold (a table, a picture, raw HTML) is edited as Markdown:
 *  opening and closing it in the rich editor used to delete that part without a word. */
export function TextEditor(props: EditorProps) {
  return richCanHold(props.markdown) ? <RichEditor {...props} /> : <MarkdownEditor {...props} />;
}

/** The block's Markdown in a plain box, for blocks the rich-text editor would damage. */
function MarkdownEditor({ markdown, onCommit, onCancel }: EditorProps) {
  const [v, setV] = useState(markdown);
  const ref = useRef<HTMLTextAreaElement>(null);
  const done = useRef(false);
  const commit = () => {
    if (done.current) return;
    done.current = true;
    onCommit(v.trim() === markdown.trim() ? markdown : v);
  };
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, []);
  return (
    <div onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) commit(); }}>
      <div className="ed-toolbar" data-testid="markdown-editor-bar">
        <span style={{ color: "#e5e5ea", fontSize: 12, padding: "4px 6px" }}
          title="This block has a table, a picture or HTML, which the rich-text editor can't hold">Markdown</span>
        <button type="button" title="Done (Ctrl+Enter)" style={{ marginLeft: 4, fontWeight: 600, fontSize: 12 }}
          onMouseDown={(e) => { e.preventDefault(); commit(); }}>Done</button>
      </div>
      <textarea ref={ref} value={v} data-testid="markdown-editor" spellCheck
        style={{ display: "block", width: "100%", minHeight: 80, resize: "vertical", border: 0, outline: "none", background: "transparent",
          color: "inherit", font: "13px/1.5 'JetBrains Mono', Consolas, monospace" }}
        onChange={(e) => {
          setV(e.target.value);
          e.target.style.height = "auto";
          e.target.style.height = `${e.target.scrollHeight + 2}px`;
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") { e.preventDefault(); done.current = true; onCancel(); }
          else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); commit(); }
        }} />
    </div>
  );
}

function RichEditor({ markdown, onCommit, onCancel, placeholder = "Write…" }: EditorProps) {
  const committed = useRef(false);
  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3] }, link: { openOnClick: false } }),
      Placeholder.configure({ placeholder }),
      Sup, Sub,
    ],
    content: mdToHtml(markdown),
    autofocus: "end",
    editorProps: { attributes: { class: "cd-md", "data-testid": "text-editor" } },
  });

  const commit = () => {
    if (committed.current || !editor) return;
    committed.current = true;
    onCommit(htmlToMd(editor.getHTML()));
  };

  useEffect(() => {
    if (!editor) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        committed.current = true;
        onCancel();
      } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        commit();
      }
    };
    const dom = editor.view.dom;
    dom.addEventListener("keydown", onKey);
    return () => dom.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor]);

  if (!editor) return null;
  const btn = (on: boolean, run: () => void, icon: React.JSX.Element, title: string) => (
    <button type="button" className={on ? "on" : ""} title={title}
      onMouseDown={(e) => { e.preventDefault(); run(); }}>{icon}</button>
  );
  const ch = () => editor.chain().focus();
  return (
    <div onBlur={(e) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node)) commit();
    }}>
      <div className="ed-toolbar">
        {btn(editor.isActive("bold"), () => ch().toggleBold().run(), <Bold size={14} />, "Bold (Ctrl+B)")}
        {btn(editor.isActive("italic"), () => ch().toggleItalic().run(), <Italic size={14} />, "Italic (Ctrl+I)")}
        {btn(editor.isActive("strike"), () => ch().toggleStrike().run(), <Strikethrough size={14} />, "Strikethrough")}
        {btn(editor.isActive("heading", { level: 2 }), () => ch().toggleHeading({ level: 2 }).run(), <Heading2 size={14} />, "Subheading")}
        {btn(editor.isActive("heading", { level: 3 }), () => ch().toggleHeading({ level: 3 }).run(), <Heading3 size={14} />, "Minor heading")}
        {btn(editor.isActive("bulletList"), () => ch().toggleBulletList().run(), <List size={14} />, "Bullet list")}
        {btn(editor.isActive("orderedList"), () => ch().toggleOrderedList().run(), <ListOrdered size={14} />, "Numbered list")}
        {btn(editor.isActive("blockquote"), () => ch().toggleBlockquote().run(), <Quote size={14} />, "Quote")}
        {btn(editor.isActive("code"), () => ch().toggleCode().run(), <Code size={14} />, "Inline code")}
        {btn(editor.isActive("superscript"), () => ch().toggleMark("superscript").run(), <Superscript size={14} />, "Superscript")}
        {btn(editor.isActive("subscript"), () => ch().toggleMark("subscript").run(), <Subscript size={14} />, "Subscript")}
        {btn(editor.isActive("link"), () => {
          const prev = editor.getAttributes("link").href || "https://";
          const url = window.prompt("Link URL (empty to remove)", prev);
          if (url === null) return;
          if (!url) ch().unsetLink().run();
          else ch().extendMarkRange("link").setLink({ href: url }).run();
        }, <Link2 size={14} />, "Link")}
        <button type="button" title="Done (Ctrl+Enter)" style={{ marginLeft: 4, fontWeight: 600, fontSize: 12 }}
          onMouseDown={(e) => { e.preventDefault(); commit(); }}>Done</button>
      </div>
      <EditorContent editor={editor} />
    </div>
  );
}
