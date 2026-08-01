// API calls for the backend's incidents module (backend/api/incident_routes.py).
import { json, url } from "./httpClient";
import type { Incident } from "./types";

export const incidentsApi = {
  incidents: (limit = 50, platform?: string) => {
    const p = new URLSearchParams({ limit: String(limit) });
    if (platform) p.set("platform", platform);
    return fetch(url(`/incidents?${p}`)).then(json<{ items: Incident[] }>);
  },
};
