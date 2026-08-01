// API calls for the backend's analysis module (backend/api/analysis_routes.py).
import { json, post } from "./httpClient";
import type { JobStatus } from "./types";

// Catch-up trigger: analyses every approved-but-unanalysed profile for the
// client across every platform. There is no per-url / per-platform manual
// analysis call -- approving a single profile already auto-queues its own
// platform's analysis (see backend/services/profile_service.py).
export const analysisApi = {
  analyse: (body: { client_id: string; callback_url?: string }) =>
    post("/analysis", body).then(json<{ job_id: string; status: JobStatus }>),
};
