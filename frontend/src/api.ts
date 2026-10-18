import type { AppConfig, Block, Comment, Doc, DocSummary, Suggestion, Upload, VersionTree } from "./types";
import { withBase } from "./base";

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(withBase(url), {
    method,
    headers: body instanceof FormData || body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      msg = j.detail || msg;
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  config: () => req<AppConfig>("GET", "/api/config"),
  listDocs: () => req<DocSummary[]>("GET", "/api/docs"),
  createDoc: (body: { title?: string; template?: string; settings?: any }) => req<Doc>("POST", "/api/docs", body),
  getDoc: (id: string) => req<Doc>("GET", `/api/docs/${id}`),
  rev: (id: string) => req<{ rev: number; running: boolean }>("GET", `/api/docs/${id}/rev`),
  patchDoc: (id: string, body: { title?: string; settings?: any }) => req<Doc>("PATCH", `/api/docs/${id}`, body),
  deleteDoc: (id: string) => req("DELETE", `/api/docs/${id}`),
  duplicateDoc: (id: string) => req<Doc>("POST", `/api/docs/${id}/duplicate`),
  reportLayout: (id: string, body: any) => req("POST", `/api/docs/${id}/layout`, body),

  addBlock: (docId: string, body: { type: string; content?: any; after?: string | null; layout?: any }) =>
    req<Block>("POST", `/api/docs/${docId}/blocks`, body),
  patchBlock: (id: string, body: { content?: any; refs?: any; layout?: any; note?: string; base?: string; author?: string }) =>
    req<Block & { conflict?: boolean }>("PATCH", `/api/blocks/${id}`, body),
  deleteBlock: (id: string) => req("DELETE", `/api/blocks/${id}`),
  moveBlock: (id: string, after: string | null) => req<Block>("POST", `/api/blocks/${id}/move`, { after }),
  duplicateBlock: (id: string) => req<Block>("POST", `/api/blocks/${id}/duplicate`),
  versions: (id: string) => req<VersionTree>("GET", `/api/blocks/${id}/versions`),
  checkout: (id: string, body: { version_id?: string; direction?: "back" | "forward" }) =>
    req<Block>("POST", `/api/blocks/${id}/checkout`, body),

  addComment: (docId: string, body: { body: string; block_id?: string | null; for_agent?: boolean }) =>
    req<Comment>("POST", `/api/docs/${docId}/comments`, body),
  replyComment: (id: string, reply: string, resolve = false) =>
    req<Comment>("PATCH", `/api/comments/${id}`, { reply, resolve }),
  setCommentStatus: (id: string, status: "open" | "resolved") => req<Comment>("PATCH", `/api/comments/${id}`, { status }),
  deleteComment: (id: string) => req("DELETE", `/api/comments/${id}`),

  accept: (id: string, payload?: any) => req<Suggestion>("POST", `/api/suggestions/${id}/accept`, { payload }),
  reject: (id: string, reason = "") => req<Suggestion>("POST", `/api/suggestions/${id}/reject`, { reason }),
  bulk: (docId: string, action: "accept-all" | "reject-all") => req("POST", `/api/docs/${docId}/suggestions/${action}`),

  upload: (docId: string, file: File, purpose: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("purpose", purpose);
    return req<Upload>("POST", `/api/docs/${docId}/uploads`, fd);
  },
  deleteUpload: (id: string) => req("DELETE", `/api/uploads/${id}`),
  messages: (docId: string) => req<any[]>("GET", `/api/docs/${docId}/messages`),
  renderStatus: () => req<{ available: boolean; hint: string | null }>("GET", "/api/render/status"),
};

export type Frame = { type: string; [k: string]: any };

/** Stream one agent turn (SSE frames from langstage-core's AG-UI stream). */
export async function streamChat(docId: string, message: string, onFrame: (f: Frame) => void,
  signal?: AbortSignal): Promise<void> {
  const r = await fetch(withBase(`/api/docs/${docId}/chat`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    signal,
  });
  if (!r.ok || !r.body) {
    let msg = `${r.status}`;
    try {
      msg = (await r.json()).detail || msg;
    } catch {
      /* ignore */
    }
    onFrame({ type: "error", error: msg });
    return;
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) {
          try {
            onFrame(JSON.parse(line.slice(6)));
          } catch {
            /* partial */
          }
        }
      }
    }
  }
}
