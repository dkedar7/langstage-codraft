import { useCallback, useEffect, useRef, useState } from "react";
import { withBase } from "../base";

export interface Peer {
  client_id: string;
  name: string;
  color: string;
  block_id: string | null;
  editing: boolean;
}

const PALETTE = ["#2563eb", "#059669", "#d97706", "#db2777", "#7c3aed", "#0891b2", "#dc2626", "#65a30d"];

function load(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function save(key: string, v: string) {
  try { localStorage.setItem(key, v); } catch { /* private mode */ }
}

/** This browser's identity for presence: a stable id, a display name and a colour. */
export function useIdentity(serverName?: string | null) {
  const [id] = useState(() => {
    // One id per tab, so two tabs of the same person show as two collaborators.
    const k = "codraft.client";
    let v = sessionStorage.getItem(k);
    if (!v) {
      v = `c_${Math.random().toString(36).slice(2, 10)}`;
      sessionStorage.setItem(k, v);
    }
    return v;
  });
  const [name, setNameState] = useState(() => load("codraft.name") || `Guest ${Math.floor(10 + Math.random() * 90)}`);
  const color = PALETTE[Math.abs([...id].reduce((h, c) => h * 31 + c.charCodeAt(0), 7)) % PALETTE.length];
  const setName = (n: string) => {
    if (serverName) return; // the host app owns identity
    const v = n.trim().slice(0, 40) || name;
    setNameState(v);
    save("codraft.name", v);
  };
  useEffect(() => { if (!serverName) save("codraft.name", name); }, [name, serverName]);
  return { id, name: serverName || name, color, setName, managed: !!serverName };
}

/**
 * Live document state over SSE (`/api/docs/:id/events`): revision changes trigger a
 * refresh, and presence lists who else is here. Falls back to polling if SSE fails.
 * Heartbeats our own presence (selected block, editing flag) every few seconds.
 */
export function useLive(docId: string, me: { id: string; name: string; color: string },
  selected: string | null, editing: string | null, onRev: (rev: number) => void) {
  const [peers, setPeers] = useState<Peer[]>([]);
  const [running, setRunning] = useState(false);
  const [connected, setConnected] = useState(false);
  const onRevRef = useRef(onRev);
  onRevRef.current = onRev;

  useEffect(() => {
    let es: EventSource | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    let closed = false;
    const handle = (snap: { rev: number; presence: Peer[]; running: boolean }) => {
      onRevRef.current(snap.rev);
      setPeers(snap.presence.filter((p) => p.client_id !== me.id));
      setRunning(snap.running);
    };
    const startPolling = () => {
      if (poll || closed) return;
      poll = setInterval(async () => {
        try {
          const r = await fetch(withBase(`/api/docs/${docId}/events?once=1`));
          const text = await r.text();
          const line = text.split("\n").find((l) => l.startsWith("data: "));
          if (line) handle(JSON.parse(line.slice(6)));
          setConnected(true);
        } catch {
          setConnected(false);
        }
      }, 2000);
    };
    try {
      es = new EventSource(withBase(`/api/docs/${docId}/events`));
      es.onopen = () => setConnected(true);
      es.onmessage = (e) => {
        try { handle(JSON.parse(e.data)); } catch { /* ignore */ }
      };
      es.onerror = () => {
        setConnected(false);
        if (es && es.readyState === EventSource.CLOSED) startPolling();
      };
    } catch {
      startPolling();
    }
    return () => {
      closed = true;
      es?.close();
      if (poll) clearInterval(poll);
    };
  }, [docId, me.id]);

  const beat = useCallback((leave = false) => {
    const body = JSON.stringify({ client_id: me.id, name: me.name, color: me.color, block_id: selected,
      editing: !!editing, leave });
    if (leave && navigator.sendBeacon) {
      navigator.sendBeacon(withBase(`/api/docs/${docId}/presence`), new Blob([body], { type: "application/json" }));
      return;
    }
    fetch(withBase(`/api/docs/${docId}/presence`), { method: "POST", headers: { "Content-Type": "application/json" }, body })
      .catch(() => {});
  }, [docId, me.id, me.name, me.color, selected, editing]);

  useEffect(() => {
    beat();
    const t = setInterval(() => beat(), 5000);
    return () => clearInterval(t);
  }, [beat]);

  useEffect(() => {
    const leave = () => beat(true);
    window.addEventListener("pagehide", leave);
    return () => {
      window.removeEventListener("pagehide", leave);
      leave();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  return { peers, running, connected };
}
