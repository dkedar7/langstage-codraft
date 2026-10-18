import type React from "react";
import { Placeholder } from "@tiptap/extensions";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Bold, Code, Heading2, Heading3, Italic, Link2, List, ListOrdered, Quote, Strikethrough } from "lucide-react";
import { useEffect, useRef } from "react";
import { htmlToMd, mdToHtml } from "../render/markdown";

/** Rich-text editing for text and callout blocks. Markdown in, Markdown out. */
export function TextEditor({ markdown, onCommit, onCancel, placeholder = "Write…" }: {
  markdown: string;
  onCommit: (md: string) => void;
  onCancel: () => void;
  placeholder?: string;
}) {
  const committed = useRef(false);
  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3] }, link: { openOnClick: false } }),
      Placeholder.configure({ placeholder }),
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
