// API calls for the backend's health module (backend/api/health_routes.py).
import { json, url } from "./httpClient";
import type { PlatformHealth } from "./types";

// GET /health/platforms merges the static registry entry with its rolling
// health score (backend/services/health_service.py) into one object per platform.
export const healthApi = {
  platformsHealth: () => fetch(url("/health/platforms")).then(json<{ items: PlatformHealth[] }>),
  ready: () => fetch(url("/health/ready")).then(json<{ status: string; mongo: boolean }>),
};
