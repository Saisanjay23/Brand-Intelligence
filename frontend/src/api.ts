// Typed mirror of the actual backend contract (backend/api/routes_*.py).
// This backend is a headless engine meant to be driven by another backend
// (see backend/main.py's own docstring) -- no /api prefix, no WebSocket
// (dropped per backend/docs/adr/0002), no bulk "list all clients" route, no
// export/preset endpoints. This file only ever calls what actually exists.
//
// Every call goes through API_BASE (see .env.example / vite-env.d.ts) so
// this file is the only place that knows where the backend lives. Deleting
// or redeploying either side never touches the other: the backend has no
// reference back to this app, and this app only ever speaks HTTP to
// whatever VITE_API_BASE_URL points at (same-origin, by default, when empty).

export type Status = "pending" | "approved" | "rejected";
export type Priority = "High" | "Medium" | "Low";
export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";
export type JobKind = "discovery" | "analysis";

export interface Client {
  client_id: string;
  name: string;
  keywords: string[];
  cron?: string | null;
  created_at?: string;
}

export interface Job {
  id: string;
  kind: JobKind;
  client_id: string;
  platform: string | null; // null = every ready platform (discovery always is)
  params: { keywords?: string[]; tabs?: string[]; max_seconds?: number; concurrency?: number; delay?: number; [k: string]: unknown };
  status: JobStatus;
  message: string;
  found: number;
  total: number;
  new_profiles: number;
  error: string;
  started: string;
  finished: string;
  last_seq: number;
}

export interface JobEvent {
  seq: number;
  job_id: string;
  type: string; // queued|running|progress|item|done|failed|cancelled
  message: string;
  found: number;
  total: number;
  ts: string;
}

// One shape for both response variants of GET /profiles (card when
// phase=discovery/omitted, full when phase=analysis) -- the fields each
// variant doesn't carry are simply undefined.
export interface Profile {
  id: string;
  platform: string;
  url: string;
  status: Status;
  has_logo: boolean;
  phase?: "discovery" | "analysis";
  // card fields (discovery)
  profile_name?: string;
  profile_image_url?: string;
  risk_score?: number | null;
  priority?: Priority | null;
  comments?: string | null;
  followers?: number | null;
  // full fields (phase=analysis)
  client_id?: string;
  client_name?: string;
  keyword?: string;
  username?: string;
  location?: string;
  last_post_date?: string | null;
}

export interface ProfilePatch {
  status?: Status;
  priority?: Priority;
  comments?: string;
  followers?: number;
  location?: string;
  last_post_date?: string;
  display_name?: string;
}

export interface SessionItem {
  id: string;
  identifier: string;
  status: string;
  rate_limited_until: number;
  last_used: number;
  cookie_count: number;
  proxy_host: string;
}

export interface SessionInfo {
  platform: string;
  name: string;
  state: "ready" | "missing" | "incomplete" | "unreadable" | "expired" | "checkpointed";
  kind: "cookies" | "api-key" | "mtproto";
  can_login: boolean;
  cookie_count: number;
  sessions: SessionItem[];
  pool_total: number;
  pool_ready: number;
  expires: string;
  message: string;
  last_verified: string;
  login?: { status: string; message: string; started: string; finished: string };
}

// GET /health/platforms merges the static registry entry with its rolling
// health score (backend/engine/health.py) into one object per platform.
export interface PlatformHealth {
  platform: string;
  name: string;
  enabled: boolean;
  session_state: string;
  state: "unknown" | "healthy" | "degraded" | "critical";
  score: number;
  ok: number;
  partial: number;
  bad: number;
  total: number;
  last_error: string;
  last_seen: number;
}

export interface Incident {
  id: string;
  platform: string;
  kind: string;
  scope: string; // client_id, or "-- all clients --" for a session-check incident
  job_id: string;
  error_type: string;
  message: string;
  cause: string;
  fix: string;
  url?: string;
  ts: string;
}

// Empty by default (same-origin relative calls); set VITE_API_BASE_URL to
// point this app at a backend hosted elsewhere. Trailing slash stripped so
// `${API_BASE}/clients` never doubles up.
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

const url = (path: string) => `${API_BASE}${path}`;

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const d = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(d.detail ?? `request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

const post = (path: string, body: unknown) =>
  fetch(url(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const api = {
  // ---------- clients ----------
  // No GET /clients list route exists on this backend -- only upsert-one and
  // fetch-one-by-id. A client picker has nothing server-side to enumerate;
  // callers are expected to know their own client_id (their own SaaS's
  // customer/org id), same as the caller this engine was actually built for.
  upsertClient: (body: { client_id: string; name: string; keywords?: string[]; cron?: string | null }) =>
    post("/clients", { keywords: [], ...body }).then(json<Client>),
  getClient: (clientId: string) =>
    fetch(url(`/clients/${encodeURIComponent(clientId)}`)).then(json<Client>),

  // ---------- discovery / analysis ----------
  // Always sweeps every platform with a ready session -- there is no
  // per-platform discovery call on this backend.
  discover: (body: {
    client_id: string;
    client_name: string;
    keywords: string[];
    tabs?: string[];
    max_results?: number;
    max_seconds?: number;
    concurrency?: number;
    callback_url?: string;
  }) => post("/discovery", body).then(json<{ job_id: string; status: JobStatus }>),

  // Catch-up trigger: analyses every approved-but-unanalysed profile for the
  // client across every platform. There is no per-url / per-platform manual
  // analysis call -- approving a single profile already auto-queues its own
  // platform's analysis (see backend/api/routes_profiles.py).
  analyse: (body: { client_id: string; callback_url?: string }) =>
    post("/analysis", body).then(json<{ job_id: string; status: JobStatus }>),

  // ---------- jobs (poll, no WebSocket -- see docs/adr/0002) ----------
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

  // ---------- profiles ----------
  profiles: (q: {
    client_id: string;
    status?: string;
    phase?: string;
    platform?: string;
    limit?: number;
    offset?: number;
  }) => {
    const p = new URLSearchParams({ client_id: q.client_id });
    if (q.status) p.set("status", q.status);
    if (q.phase) p.set("phase", q.phase);
    if (q.platform) p.set("platform", q.platform);
    p.set("limit", String(q.limit ?? 100));
    p.set("offset", String(q.offset ?? 0));
    return fetch(url(`/profiles?${p}`)).then(json<{ items: Profile[]; total: number }>);
  },
  profile: (id: string) => fetch(url(`/profiles/${id}`)).then(json<Profile>),
  patchProfile: (id: string, fields: ProfilePatch) =>
    fetch(url(`/profiles/${id}`), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    }).then(json<Profile>),

  // ---------- sessions ----------
  // No bulk "all sessions" route -- one call per known platform id (get the
  // id list from platformsHealth()).
  sessionStatus: (platform: string) => fetch(url(`/sessions/${platform}`)).then(json<SessionInfo>),
  saveCookies: (platform: string, blob: string, identifier = "") =>
    post(`/sessions/${platform}/cookies`, { blob, identifier }).then(json<SessionInfo>),
  saveApiKey: (platform: string, key: string) =>
    post(`/sessions/${platform}/api-key`, { key }).then(json<SessionInfo>),
  launchLogin: (platform: string, timeoutS = 300, identifier = "") =>
    post(`/sessions/${platform}/login`, { timeout_s: timeoutS, identifier }).then(
      json<{ platform: string; status: string; message: string; started: string; finished: string }>,
    ),
  checkSessionNow: (platform: string) =>
    post(`/sessions/${platform}/check`, {}).then(json<{ ok: boolean; detail: string }>),
  setSessionProxy: (
    platform: string,
    sessionId: string,
    proxy: { server: string; username?: string; password?: string; timezone_id?: string },
  ) =>
    fetch(url(`/sessions/${platform}/${sessionId}/proxy`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proxy }),
    }).then(json<SessionInfo>),
  // Backend has no separate DELETE-proxy route -- clearing is PUT with
  // proxy: null (see routes_sessions.py::set_proxy).
  clearSessionProxy: (platform: string, sessionId: string) =>
    fetch(url(`/sessions/${platform}/${sessionId}/proxy`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proxy: null }),
    }).then(json<SessionInfo>),
  deleteSessionItem: (platform: string, sessionId: string) =>
    fetch(url(`/sessions/${platform}/${sessionId}`), { method: "DELETE" }).then(json<SessionInfo>),
  deleteSessionPool: (platform: string) =>
    fetch(url(`/sessions/${platform}`), { method: "DELETE" }).then(json<SessionInfo>),

  // ---------- health / platform registry ----------
  platformsHealth: () => fetch(url("/health/platforms")).then(json<{ items: PlatformHealth[] }>),
  ready: () => fetch(url("/health/ready")).then(json<{ status: string; mongo: boolean }>),

  // ---------- incidents ----------
  incidents: (limit = 50, platform?: string) => {
    const p = new URLSearchParams({ limit: String(limit) });
    if (platform) p.set("platform", platform);
    return fetch(url(`/incidents?${p}`)).then(json<{ items: Incident[] }>);
  },
};
