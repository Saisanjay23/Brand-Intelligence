// API calls for the backend's jobs module (backend/api/job_routes.py).
import { json, post, url } from "./httpClient";
import type { Job, JobEvent } from "./types";

// Poll, no WebSocket -- see backend/docs/adr/0002-polling-plus-webhook-over-websocket.md
export const jobsApi = {
  jobs: (clientId = "", limit = 25) =>
    fetch(url(`/jobs?client_id=${encodeURIComponent(clientId)}&limit=${limit}`)).then(
      json<{ items: Job[] }>,
    ),
  job: (id: string) => fetch(url(`/jobs/${id}`)).then(json<Job>),
  jobEvents: (id: string, afterSeq = 0) =>
    fetch(url(`/jobs/${id}/events?after_seq=${afterSeq}`)).then(
      json<{ items: JobEvent[]; last_seq: number }>,
    ),
  cancelJob: (id: string) => post(`/jobs/${id}/cancel`, {}).then(json<{ cancelled: boolean }>),
};
