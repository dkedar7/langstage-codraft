/**
 * Where Codraft is served from. The server injects `window.__CODRAFT__.base` into
 * index.html (for example "/api/proxy/codraft" when a host app proxies Codraft under a
 * sub-path); standalone it is "". Every URL the app builds goes through `withBase`.
 */
declare global {
  interface Window {
    __CODRAFT__?: { base?: string };
  }
}

export const BASE: string = (typeof window !== "undefined" && window.__CODRAFT__?.base || "").replace(/\/+$/, "");

/** "/api/x" -> "<base>/api/x". Absolute URLs (http:, data:, blob:) pass through. */
export function withBase(path: string): string {
  if (!path || /^(https?:|data:|blob:)/i.test(path) || !path.startsWith("/")) return path;
  return BASE + path;
}

/** The app-relative path of the current page ("/d/doc_1"), without the base. */
export function appPath(): string {
  const p = window.location.pathname;
  return BASE && p.startsWith(BASE) ? p.slice(BASE.length) || "/" : p;
}

/** Query-string flags the host can pass, e.g. ?embed=1 when shown inside another app. */
export function urlFlag(name: string): boolean {
  try {
    const v = new URLSearchParams(window.location.search).get(name);
    return v === "1" || v === "true";
  } catch {
    return false;
  }
}
