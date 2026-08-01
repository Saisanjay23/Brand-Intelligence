/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative paths so the bundle can be served from any origin (see
// src/api.ts's API_BASE) -- this dev proxy is local convenience only, not
// what makes the app work. Every prefix here is a real backend router
// (backend/api/routes_*.py); none of them sit under /api -- this is a
// headless engine, not a UI-serving app (see backend/main.py's docstring).
const BACKEND = "http://127.0.0.1:8000";
const proxy = Object.fromEntries(
  ["/clients", "/discovery", "/analysis", "/jobs", "/profiles", "/sessions", "/health", "/incidents", "/metrics", "/docs", "/redoc", "/openapi.json"]
    .map((path) => [path, { target: BACKEND, changeOrigin: true }]),
);

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // e2e/ is Playwright's (a different `test`/`expect` API entirely) --
    // without this exclusion Vitest tries to parse it too and fails
    exclude: ["e2e/**", "node_modules/**"],
  },
});
