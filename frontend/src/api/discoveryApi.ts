// API calls for the backend's discovery module (backend/api/discovery_routes.py).
import { json, post } from "./httpClient";
import type { JobStatus } from "./types";

// Always sweeps every platform with a ready session -- there is no
// per-platform discovery call on this backend. The client must already
// exist (see clientsApi.upsertClient) -- this no longer creates one.
export const discoveryApi = {
  discover: (body: {
    client_id: string;
    keywords: string[];
    tabs?: string[];
    max_results?: number;
    max_seconds?: number;
    concurrency?: number;
    callback_url?: string;
  }) => post("/discovery", body).then(json<{ job_id: string; status: JobStatus }>),
};
