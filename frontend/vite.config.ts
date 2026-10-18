import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const backend = process.env.CODRAFT_BACKEND || "http://127.0.0.1:8060";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../langstage_codraft/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 3000,
  },
  server: {
    port: 5173,
    proxy: { "/api": backend, "/agui": backend },
  },
  test: { include: ["src/**/*.test.ts"] },
} as any);
