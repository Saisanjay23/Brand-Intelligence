import { defineConfig } from "@playwright/test";

// Runs against the *real* built app served by the real FastAPI backend --
// on port 8010 (not 8000) so this never collides with a dev server someone
// already has running. The smoke test itself decides whether it's safe to
// exercise a given platform for real (YouTube: yes, no session/stealth risk)
// or must skip (anything needing a live browser session).
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false, // one shared backend + Mongo client, no point racing
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:8010",
    trace: "retain-on-failure",
  },
  webServer: {
    // cwd is the repo root (backend/ lives there); build the frontend into
    // dist/ first, then serve both from the same real FastAPI process.
    command:
      "npm run build --prefix frontend && python -m uvicorn backend.api.app:app --port 8010",
    cwd: "..",
    url: "http://127.0.0.1:8010/api/platforms",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
