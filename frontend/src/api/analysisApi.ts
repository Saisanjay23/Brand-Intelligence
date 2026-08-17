// API calls for the backend's analysis module (backend/api/analysis_routes.py).
import { json, post } from "./httpClient";
import type { JobStatus } from "./types";

// Manual trigger: by default a catch-up sweep, analyses every
// approved-but-unanalysed profile for the client across every platform.
// There is no per-url / per-platform manual analysis call, approving a
// single profile already auto-queues its own platform's analysis (see
// backend/services/profile_service.py).
//
// `force: true` instead re-analyses EVERY currently-approved profile,
// including ones a previous run already scored, this is what makes an
// explicit "run analysis again" click actually do something even when the
// normal backlog (what catch-up alone would pick up) is already empty.
export const analysisApi = {
  analyse: (body: {
    client_id: string;
    callback_url?: string;
    force?: boolean;
    // omitted/undefined analyses every ready platform, as before, set to
    // one platform id to scope the run to just that platform (the Re-run
    // Analysis button's "All Platforms" vs. one-platform selector)
    platform?: string;
  }) => post("/analysis", body).then(json<{ job_id: string; status: JobStatus }>),
};
