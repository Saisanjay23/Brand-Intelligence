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
  // optional URL of the brand's own real logo -- shown side-by-side with a
  // discovered profile's avatar during analysis triage.
  logo_url?: string;
  name_keywords: string[];
  domain_keywords: string[];
  asset_name_individual_keywords?: string[];
  asset_name_domain_keywords?: string[];
  // platform id -> max results to scrape for this client. Missing (or 0)
  // means uncapped -- "scrape all" for that platform.
  platform_limits: Record<string, number>;
  // platform id -> {tab: max results}, for platforms with more than one
  // discovery tab -- currently only "facebook": {people, pages}. A tab
  // missing here falls back to platform_limits[platform], then uncapped.
  platform_tab_limits: Record<string, Record<string, number>>;
  // platform id -> this brand's OWN official handle there, e.g.
  // {twitter: "adanionline"}. Discovery scores each found profile's handle
  // against it -- the only automated username signal in the system, since
  // name_score only ever compares display names.
  official_handles?: Record<string, string>;
  cron?: string | null;
  created_at?: string;
}

// Four distinct not-successful outcomes, because each calls for a different
// next step:
//   skipped -- never attempted; its session wasn't ready when the sweep
//              started. Fix the session under Sessions.
//   partial -- started and covered only some of what was asked (a cap fired,
//              the session pool ran dry mid-run, a sweep stalled). The job's
//              `message` says which. Re-run to pick up the remainder.
//   failed  -- started and broke outright.
//   done    -- everything asked for was actually attempted.
export type PlatformJobStatus = "pending" | "running" | "done" | "partial" | "failed" | "skipped";

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
  // oldest event still retained server-side (the per-job event buffer is a
  // ring -- see MAX_EVENTS_PER_JOB). A client resuming from an after_seq
  // below this missed a range rather than receiving a silently short stream.
  first_seq?: number;
  platforms: Record<string, PlatformProgress>;
  // set only while status="queued" and something else holds this job's
  // platform lock -- turns an unexplained "queued" into "waiting on
  // client X's Facebook sweep" (see job_service.py's per-platform lock).
  blocked_by?: { job_id: string; client_id: string; kind: JobKind; platform: string | null } | null;
}

export interface JobEvent {
  seq: number;
  job_id: string;
  type: string; // queued|running|progress|item|done|failed|cancelled
  message: string;
  found: number;
  total: number;
  ts: string;
  client_id: string;
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
  // "profile" | "page" -- only ever set on Facebook discovery rows (the
  // one platform whose discovery distinguishes people from pages); blank
  // everywhere else.
  entity_type?: string;
  // every keyword sweep that has (re)found this profile -- discovery cards
  // only, see backend/services/profile_service.py::_to_card
  keywords?: string[];
  // 0-100 name-vs-keyword closeness (discovery-seeded, analysis-refined) --
  // powers the card's High/Low match badge
  name_score?: number | null;
  // 0-100 handle-vs-official-handle closeness. null/undefined means NOT
  // MEASURED (the client has no official handle set for this platform) --
  // deliberately distinct from 0, which means "measured, no resemblance".
  username_score?: number | null;
  // an analyst's own visual confirmation, set via the discovery card's
  // Validate action -- carried through to the analysis-phase record too
  logo_match?: boolean | null;
  username_match?: boolean | null;
  // set only on a profile a rediscovery just bounced from "rejected" back
  // to "pending" because its display name and/or logo actually changed
  // (see backend/database/repositories/profile_repository.py's
  // RECONSIDER_FIELDS) -- {field: {old, new}}. Cleared the moment the
  // analyst makes a fresh decision.
  changes?: Record<string, { old: unknown; new: unknown }> | null;
  changed_at?: string | null;
  // full fields (phase=analysis)
  client_id?: string;
  client_name?: string;
  keyword?: string;
  username?: string;
  location?: string;
  last_post_date?: string | null;
  // true/false/null -- computed from last_post_date against a 6-month
  // dormant-account window. null means no last-post date was ever found
  // (unknown), never "confirmed inactive" -- do not render null as Inactive.
  is_active?: boolean | null;
  // publish hold (phase=analysis only) -- see backend/docs/adr/0007-publish-hold.md.
  // A row missing these (discovery-phase, or analysed before this feature
  // existed) should be treated as already published. The hold now genuinely
  // EXPIRES on its own once publish_hold_until passes; publishing early is
  // what the Publish action is for.
  published?: boolean;
  publish_hold_until?: string | null;
  // Evidence capture taken while analysis was reading the profile -- often
  // the only surviving proof the account existed by the time a takedown
  // request is actually read. Null when no capture was taken (evidence
  // disabled, an API-only platform like YouTube/Telegram, or a run that
  // never reached the profile). Append ?download=true for an attachment.
  screenshot_url?: string | null;
  screenshot_at?: string | null;
  // "OK" | "PARTIAL" | "GONE" | "ERROR" | "CHECKPOINT" | "LOGIN_REQUIRED".
  // The last three mean the profile was never actually read -- the run is
  // retried automatically until analysis_attempts hits the server's cap.
  analysis_status?: string;
  analysis_attempts?: number;
  analysed_at?: string | null;
  // live preview of the exact record Publish writes to published_incidents
  // (see backend/services/incident_publisher.py) -- analysis phase only,
  // null when the client record is gone. Editable pre-publish via
  // ProfilePatch.incident_overrides.
  incident?: PublishedIncidentPreview | null;
}

export interface PublishedIncidentSocialProfileInfo {
  isActive: boolean;
  isSimilarName: boolean;
  isSimilarLogo: boolean;
  numberOfFollowers: number | null;
  profileName: string | null;
  location: string | null;
  profileImage: string | null;
  lastPostDate: string | null;
  posts: number | null;
}

// Not to be confused with `Incident` above (that's the unrelated
// job-failure diagnostic log) -- this is the client-facing finding this
// tool hands off on Publish.
export interface PublishedIncidentPreview {
  title: string;
  category: string;
  subCategory: string;
  assetType: string;
  source: string;
  date: string | null;
  description: string;
  riskRating: string;
  domain: string;
  orgId: string;
  assetCategory: string;
  assetName: string;
  thirdParty: boolean;
  socialProfileInfo: PublishedIncidentSocialProfileInfo;
  // how much of this record rests on something actually read
  evidence?: {
    screenshot: boolean;
    fieldsRead: number;
    analysisStatus: string;
  };
}

// GET /profiles/coverage -- "did we actually check everything?", which
// volume counts alone can't answer. `complete` is false while anything is
// still owed or was permanently given up on.
export interface Coverage {
  approved: number;
  analysed: number;
  awaiting_analysis: number;
  analysis_failed: number;
  held: number;
  with_evidence: number;
  complete: boolean;
  // profiles that will NOT be retried without intervention
  blocked: {
    id: string;
    url: string;
    platform: string;
    profile_name: string;
    reason: string;
    attempts: number;
    detail: string;
  }[];
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
  // flat dotted-path edits merged into the profile's own incident_overrides
  // (see ProfilePatch in backend/dto/profile_dto.py), e.g.
  // {"title": "..."} or {"socialProfileInfo.location": "..."}
  incident_overrides?: Record<string, unknown>;
}

export interface SessionItem {
  id: string;
  identifier: string;
  status: string;
  rate_limited_until: number;
  last_used: number;
  // how many times this session has actually been handed to a job --
  // durable, never reset, distinct from `last_used` (a timestamp, not a
  // count)
  use_count: number;
  // is a job holding this EXACT session right now, this instant -- derived
  // server-side from the real running job that acquired it (see
  // sessions/manager.py::_session_in_use), so it clears itself the moment
  // that job stops running rather than needing anything to "release" it
  in_use: boolean;
  cookie_count: number;
  proxy_host: string;
  is_api_key?: boolean;
  // consecutive failures since this session last demonstrably worked --
  // drives the graduated quarantine ladder (15m -> 1h -> 6h -> 24h), so a
  // count of 1 is a blip and 4 is a probably-burned account
  consecutive_failures?: number;
  // server-computed "could a job use this right now": not dead, and past
  // any cooldown. Distinct from `status` alone.
  available?: boolean;
}

export interface SessionInfo {
  platform: string;
  name: string;
  // "exhausted" = a complete, valid session exists but every one is
  // quarantined or cooling off right now. Distinct from "incomplete" (a
  // botched cookie export) because the fix differs: wait, or add another
  // account. This state used to be reported as "ready", so a fully dead
  // pool showed green while every job launched into it failed.
  state: "ready" | "missing" | "incomplete" | "exhausted" | "unreadable" | "expired" | "checkpointed";
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
  // an inherent caveat about this platform's scraping surface -- was
  // tracked in the backend registry but never actually surfaced anywhere.
  // Empty when there's none.
  stability_note?: string;
  // true when field-scraping during analysis isn't implemented -- running
  // analysis silently produces nothing useful. Surfaced so the UI can warn before, not after.
  analysis_stub?: boolean;
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
