import { Monitor, Moon, Sun } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

type Theme = "light" | "dark" | "auto";
const KEY = "codraft.theme";

function stored(): Theme | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" || v === "auto" ? v : null;
  } catch {
    return null;
  }
}

/** langstage-web's light / dark / auto toggle (cycles on click), remembered per browser. */
export function ThemeToggle({ initialTheme = "auto", fixed = false }: { initialTheme?: string; fixed?: boolean }) {
  const initial: Theme = ["light", "dark", "auto"].includes(initialTheme) ? initialTheme as Theme : "auto";
  const [theme, setTheme] = useState<Theme>(() => (fixed ? initial : stored() ?? initial));

  const applyTheme = useCallback((t: Theme) => {
    const isDark = t === "dark" || (t === "auto" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", isDark);
  }, []);

  useEffect(() => {
    applyTheme(theme);
    if (!fixed) try { localStorage.setItem(KEY, theme); } catch { /* private mode */ }
    if (theme === "auto") {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const handler = () => applyTheme("auto");
      mq.addEventListener("change", handler);
      return () => mq.removeEventListener("change", handler);
    }
  }, [theme, applyTheme, fixed]);

  if (fixed) return null;
  const cycle = () => setTheme((p) => (p === "light" ? "dark" : p === "dark" ? "auto" : "light"));
  const Icon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  return (
    <button onClick={cycle} data-testid="theme-toggle" title={`Theme: ${theme}`}
      className="p-1.5 rounded-md hover:bg-[var(--color-surface-3)] transition-colors">
      <Icon size={16} className="text-[var(--color-text-secondary)]" />
    </button>
  );
}
