import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  // Playwright supplies its own API responses against the static build.
  preview: { proxy: {} },
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: process.env.CM_DEV_API_URL || "http://127.0.0.1:8080", ws: true },
      "/health": process.env.CM_DEV_API_URL || "http://127.0.0.1:8080",
      "^/mcp(?:/(?:reviews|security|code))?/?(?:\\?|$)": { target: process.env.CM_DEV_API_URL || "http://127.0.0.1:8080", changeOrigin: false },
      "/oauth": process.env.CM_DEV_API_URL || "http://127.0.0.1:8080",
      "/.well-known": process.env.CM_DEV_API_URL || "http://127.0.0.1:8080",
    },
  },
});
