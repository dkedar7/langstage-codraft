import { createContext, useContext } from "react";
import type { AppConfig, Doc } from "../types";
import type { Peer } from "./useLive";

export type PanelTab = "chat" | "comments" | "review" | "block" | "document";

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
  toast: (msg: string, kind?: "info" | "error") => void;
  agentRunning: boolean;
  setAgentRunning: (b: boolean) => void;
  zoom: number;
  /** other people in this document right now */
  peers: Peer[];
  me: { id: string; name: string; color: string };
}

export const EditorContext = createContext<EditorApi | null>(null);

export function useEditorApi(): EditorApi {
  const v = useContext(EditorContext);
  if (!v) throw new Error("EditorContext missing");
  return v;
}
