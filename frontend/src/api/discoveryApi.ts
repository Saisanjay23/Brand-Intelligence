// API calls for the backend's discovery module (backend/api/discovery_routes.py).
import { json, post } from "./httpClient";
import type { JobStatus } from "./types";

// Always sweeps every platform with a ready session, there is no
// per-platform discovery call on this backend. The client must already
// exist (see clientsApi.upsertClient), this no longer creates one.
export const discoveryApi = {
  discover: (body: {
    client_id: string;
    keywords: string[];
    tabs?: string[];
    max_results?: number;
    max_seconds?: number;
    concurrency?: number;
    callback_url?: string;
    // omitted/undefined sweeps every ready platform, as before, set to
    // one platform id to scope the sweep to just that platform (the Sweep
    // button's "All Platforms" vs. one-platform selector)
    platform?: string;
    // Two or more platform ids to sweep together (the Run hub's
    // multi-select). Takes precedence over `platform` when both are sent.
    platforms?: string[];
    // Scope the sweep to one keyword category. Omitted/undefined sweeps
    // BOTH, which is the default and what every caller predating this got.
    //   "individual" -- only the client's executive/individual names
    //   "domain"     -- only its brand/domain keywords
    // Applied server-side against each keyword's own resolved category, so
    // the caller still sends the full keyword list either way.
    keyword_type?: "individual" | "domain";
  }) => post("/discovery", body).then(json<{ job_id: string; status: JobStatus }>),
  // Re-resolves name/photo for a hand-picked set of already-discovered
  // profile ids, no keyword search, just a targeted refresh (Facebook
  // only; see backend/services/discovery_service.py::_resweep_selected).
  // Fixes a card permanently stuck on a bare numeric id/no photo without
  // waiting on, or forcing, a whole new keyword sweep.
  resweepSelected: (client_id: string, profile_ids: string[]) =>
    post("/discovery", { client_id, profile_ids }).then(json<{ job_id: string; status: JobStatus }>),
};
