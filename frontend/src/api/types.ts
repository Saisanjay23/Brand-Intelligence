// Typed mirror of the actual backend contract (backend/api/*_routes.py).
// This backend is a headless engine meant to be driven by another backend
// (see backend/main.py's own docstring) -- no /api prefix, no WebSocket
// (dropped per backend/docs/adr/0002), no bulk "list all clients" route, no
// export/preset endpoints.
//
// Shared by every api/*Api.ts module -- request/response shapes live here
// once instead of being redeclared per module, since several (Job, Profile)
// are used by more than one backend resource's API file.

export type Status = "pending" | "approved" | "rejected";
export type Priority = "High" | "Medium" | "Low";
export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";
export type JobKind = "discovery" | "analysis";

export interface Client {
  client_id: string;
  name: string;
  domain: string;
  name_keywords: string[];
  domain_keywords: string[];
  // platform id -> max results to scrape for this client. Missing (or 0)
  // means uncapped -- "scrape all" for that platform.
  platform_limits: Record<string, number>;
  cron?: string | null;
  created_at?: string;
}

export type PlatformJobStatus = "pending" | "running" | "done" | "failed";

export interface PlatformProgress {
  status: PlatformJobStatus;
  processed: number;
  total: number;
  started: number | null; // epoch seconds
  updated: number | null; // epoch seconds
  eta_seconds: number | null; // server-computed, only set while running
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
  platforms: Record<string, PlatformProgress>;
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
  verified?: boolean;
  risk_score?: number | null;
  priority?: Priority | null;
  comments?: string | null;
  followers?: number | null;
  // every keyword sweep that has (re)found this profile -- discovery cards
  // only, see backend/services/profile_service.py::_to_card
  keywords?: string[];
  // 0-100 name-vs-keyword closeness (discovery-seeded, analysis-refined) --
  // powers the card's High/Low match badge
  name_score?: number | null;
  // an analyst's own visual confirmation, set via the discovery card's
  // Validate action -- carried through to the analysis-phase record too
  logo_match?: boolean | null;
  username_match?: boolean | null;
  // full fields (phase=analysis)
  client_id?: string;
  client_name?: string;
  keyword?: string;
  username?: string;
  location?: string;
  last_post_date?: string | null;
  // publish hold (phase=analysis only) -- see backend/docs/adr/0007-publish-hold.md.
  // A row missing these (discovery-phase, or analysed before this feature
  // existed) should be treated as already published.
  published?: boolean;
  publish_hold_until?: string | null;
}

export interface ProfilePatch {
  status?: Status;
  priority?: Priority;
  logo_match?: boolean;
  username_match?: boolean;
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
// health score (backend/services/health_service.py) into one object per platform.
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
