import { createContext, useContext } from "react";
import type { AppConfig, Doc } from "../types";
import type { Peer } from "./useLive";

export type PanelTab = "chat" | "comments" | "review" | "block" | "design" | "document";

export interface EditorApi {
  doc: Doc;
  cfg: AppConfig;
  refresh: () => Promise<void>;
  selected: string | null;
  select: (id: string | null) => void;
  editing: string | null;
  setEditing: (id: string | null) => void;
  tab: PanelTab;
  setTab: (t: PanelTab) => void;
  commentFilter: string | null;
  setCommentFilter: (id: string | null) => void;
  /** Send a message to the agent (switches to the chat tab). */
  ask: (message: string) => void;
  /** Run a mutation, then refresh the document; errors become a toast. */
  run: <T>(p: Promise<T>, okMsg?: string) => Promise<T | undefined>;
  toast: (msg: string, kind?: "info" | "error", action?: { label: string; run: () => void }) => void;
  /** Blocks whose content is taller than the fixed height or pin box they were given (what
   *  doesn't fit doesn't print): the height the content needs and the box it has, in px. */
  cutOff: Record<string, { natural: number; box: number }>;
  setCutOff: (c: Record<string, { natural: number; box: number }>) => void;
  /** Delete a block and offer to undo it. */
  removeBlock: (id: string) => Promise<void>;
  /** Bring a deleted block back where it was. */
  restoreBlock: (id: string) => Promise<void>;
  /** The checkpoint whose changes the Document tab is showing, if any (gh #61). */
  checkpointReview: string | null;
  /** Show what changed since a checkpoint (opens the Document tab), or close that view. */
  setCheckpointReview: (id: string | null) => void;
  agentRunning: boolean;
  setAgentRunning: (b: boolean) => void;
  zoom: number;
  /** other people in this document right now */
  peers: Peer[];
  me: { id: string; name: string; color: string };
  /** Download an export; ``extra`` adds query parameters, e.g. a Word redline's
   *  ``{since: checkpointId}`` or ``{pending: "true"}`` (gh #65). */
  exportDoc: (fmt: string, extra?: Record<string, string>) => Promise<void>;
  /** Re-read the app config (after saving or deleting a theme). */
  reloadConfig?: () => Promise<void>;
}

export const EditorContext = createContext<EditorApi | null>(null);

export function useEditorApi(): EditorApi {
  const v = useContext(EditorContext);
  if (!v) throw new Error("EditorContext missing");
  return v;
}
