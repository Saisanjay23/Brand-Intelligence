/**
 * Pure result-list logic pulled out of ResultsGrid.tsx so it can be unit
 * tested without rendering the component: the filter chain, the sort order,
 * and the "why is this cell empty" labelling.
 *
 * Scoped to what GET /profiles actually returns (backend/api/profile_routes.py)
 * -- no entity_type, has_name_match, friends, or sources fields exist on this
 * backend's response, so the old Facebook Page/People split and exact-match
 * filter have no data to work from and are gone, not stubbed.
 */

import type { Job, Profile } from "../api/types";

// ───────────────────────── per-platform job ETA ────────────────────────────
//   analysis  -- job.total = URLs to visit (once known), extrapolated from
//                the observed found/elapsed rate.
//   discovery -- there is no target count; bounded instead by the time
//                budget: every keyword x tab sweep is capped at max_seconds,
//                and only `concurrency` run at once, so total time is
//                bounded by ceil(sweepCount / concurrency) * max_seconds.
export interface EtaEstimate {
  seconds: number;
  // true when `seconds` is an upper bound (a cap that may not be reached),
  // false when it's a rate-based prediction -- formatEta() prefixes these
  // differently ("up to" vs "~") so the UI never states a guess as fact.
  ceiling: boolean;
}

export function estimateRemainingSeconds(job: Job): EtaEstimate | null {
  if (!job.started) return null;
  const startedMs = new Date(job.started).getTime();
  if (Number.isNaN(startedMs)) return null;
  const elapsed = Math.max(0, (Date.now() - startedMs) / 1000);

  if (job.kind === "analysis") {
    if (!job.total) return null;
    if (job.found <= 0) {
      // No "item" event has landed yet to extrapolate a rate from. The
      // catch-up analysis job takes no caller-supplied pacing delay (the
      // per-profile pace is an internal server setting, not echoed back in
      // params), so unlike discovery there is no ceiling fallback here --
      // simply no estimate yet, rather than guessing at a delay this backend
      // never told the caller.
      return null;
    }
    const remaining = job.total - job.found;
    if (remaining <= 0) return { seconds: 0, ceiling: false };
    if (elapsed <= 0) return null;
    const ratePerSecond = job.found / elapsed;
    return ratePerSecond > 0
      ? { seconds: remaining / ratePerSecond, ceiling: false }
      : null;
  }

  const params = job.params || {};
  const maxSeconds = Number(params.max_seconds);
  if (!maxSeconds || maxSeconds <= 0) return null;
  const keywords = params.keywords;
  const tabs = params.tabs as string[] | undefined;
  const keywordCount = Array.isArray(keywords) ? keywords.length : 1;
  const tabCount = Array.isArray(tabs) ? tabs.length : 1;
  const concurrency = Number(params.concurrency) || 1;
  const sweepCount = Math.max(1, keywordCount * tabCount);
  const totalBudget = Math.ceil(sweepCount / concurrency) * maxSeconds;
  return { seconds: Math.max(0, totalBudget - elapsed), ceiling: true };
}

/** "~2m left" / "up to 45s left". */
export function formatEta(estimate: EtaEstimate | null): string {
  if (estimate === null) return "";
  const { seconds, ceiling } = estimate;
  if (seconds < 1) return ceiling ? "wrapping up" : "any moment";
  const prefix = ceiling ? "up to " : "~";
  if (seconds < 60) return `${prefix}${Math.ceil(seconds)}s left`;
  const mins = Math.ceil(seconds / 60);
  if (mins < 60) return `${prefix}${mins}m left`;
  const hrs = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${prefix}${hrs}h${remMins ? ` ${remMins}m` : ""} left`;
}

export interface ResultFilters {
  status: string;
  priority: string;
  phase: string;
}

export interface ExtraFilters {
  keywordFilter: string;
  searchQuery: string;
  // discovery-only "how closely does the name match the keyword" badge
  // filter -- optional so existing callers/tests that only set the two
  // fields above keep compiling unchanged. Threshold mirrors backend's
  // shared/models/scoring.py::NAME_THRESHOLD (80).
  matchLevel?: "" | "high" | "low";
  // Facebook-discovery-only people/pages filter -- entity_type is blank
  // on every other platform, so this is a no-op there.
  entityType?: "" | "profile" | "page";
  // Individual-name-keyword vs domain-keyword match, classified the same
  // way services/incident_publisher.py::_category_and_asset_name does --
  // by set-membership against the client's own configured keyword lists,
  // not a stored per-profile field. Available in both discovery and
  // analysis views.
  keywordMatchType?: "" | "individual" | "domain";
}

/** The client's own configured keyword lists, used only to classify which
 * category (individual vs domain) a row's matched keyword(s) fall under
 * for `keywordMatchType` filtering -- see `ExtraFilters.keywordMatchType`. */
export interface ClientKeywordSets {
  nameKeywords: Set<string>;
  domainKeywords: Set<string>;
}

/** A row's matched keyword string(s), independent of phase: discovery rows
 * carry every keyword sweep that found them as an array, analysis rows
 * carry a single comma-joined string. */
function rowMatchedKeywords(r: Profile): string[] {
  if (r.phase === "discovery") return r.keywords || [];
  return (r.keyword || "").split(",").map((s) => s.trim()).filter(Boolean);
}

export type SortOrder = "recent" | "past";

// ─────────────────────── keyword relevance ranking ─────────────────────────
// Discovery has no risk score yet (that's an analysis-phase concept -- every
// discovery row ties at 0), so sorting it by risk_score is a no-op. What an
// analyst actually wants first is the profile whose name most closely
// matches the keyword that found it.

function normalizeForMatch(s: string): string {
  return s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Lower is closer. Deliberately simple word/substring heuristics rather than
 * a fuzzy-matching library on the client. */
function relevanceScore(name: string, keyword: string): number {
  const n = normalizeForMatch(name);
  const k = normalizeForMatch(keyword);
  if (!n || !k) return 100;
  if (n === k) return 0;
  if (n.startsWith(k) || k.startsWith(n)) return 1;
  if (n.includes(k)) return 2;
  const kWords = k.split(" ").filter(Boolean);
  const nWords = new Set(n.split(" ").filter(Boolean));
  const overlap = kWords.filter((w) => nWords.has(w)).length;
  if (overlap > 0) return Math.max(3, 10 - overlap * 3);
  return 50;
}

/** The profile's own display name, whichever phase's field carries it. */
function profileName(r: Profile): string {
  return r.profile_name || r.username || "";
}

/** The closest this row's own name comes to any keyword worth judging it
 * against: the active keyword filter if one is picked, otherwise the single
 * keyword that found this profile (analysis phase only -- discovery cards
 * don't carry it at all, see backend/services/profile_service.py::_to_card). */
export function keywordRelevance(r: Profile, activeKeyword?: string): number {
  const name = profileName(r);
  const candidate = (activeKeyword && activeKeyword.trim()) || r.keyword || "";
  return relevanceScore(name, candidate);
}

/** "Page" / "People" isn't available -- this backend's /profiles response
 * carries no entity_type at all, so reach is always labelled "followers". */
export function reachLabel(): "followers" {
  return "followers";
}

/** A DEFENSIVE second pass over rows the server has already filtered.
 *
 * Every predicate here now has a server-side counterpart applied before
 * limit/offset (see profilesApi.profiles and backend profile_repository.find),
 * because a filter that only narrows the current page is not a filter: the
 * pager kept reporting the unfiltered total, so "High priority" over 500
 * rows showed the High rows within page 1 and still claimed 500 results.
 *
 * This is kept, rather than deleted, for the window where local state is
 * ahead of the server -- an optimistic status change applied before its
 * PATCH lands, or rows already in memory during a live-poll refresh. It
 * must stay a strict subset of what the server does, or it will hide rows
 * the server legitimately returned.
 *
 * Every predicate is independent (AND logic). */
export function filterResults(
  rows: Profile[],
  filters: ResultFilters,
  extra: ExtraFilters,
  platform?: string,
  clientKeywords?: ClientKeywordSets,
): Profile[] {
  return rows.filter((r) => {
    if (platform && platform !== "all" && r.platform !== platform) return false;
    if (filters.status && r.status !== filters.status) return false;
    if (filters.priority && r.priority !== filters.priority) return false;
    if (filters.phase) {
      if (filters.phase === "discovery") {
        if (r.phase !== "discovery" && r.status !== "approved") return false;
      } else {
        if (r.phase !== filters.phase) return false;
      }
    }

    if (extra.keywordFilter.trim()) {
      const kf = extra.keywordFilter.trim();
      if (r.phase === "discovery") {
        // discovery cards carry every keyword sweep that has (re)found
        // them as an array -- an analyst picks one exact keyword from a
        // list (see GET /profiles?keyword=, the server already scoped the
        // fetch to it; this is just a defensive re-check for whatever's
        // in memory, e.g. during live-poll refreshes).
        if (!(r.keywords || []).includes(kf)) return false;
      } else if (!(r.keyword || "").toLowerCase().includes(kf.toLowerCase())) {
        // analysis-phase keyword field is a single comma-joined string
        // with no server-side index to filter on -- substring match over
        // whatever page is currently loaded, not an exact picker.
        return false;
      }
    }

    if (extra.matchLevel) {
      const score = r.name_score;
      if (score === null || score === undefined) return false;
      const level = score >= 80 ? "high" : "low";
      if (level !== extra.matchLevel) return false;
    }

    if (extra.entityType && r.entity_type !== extra.entityType) return false;

    if (extra.keywordMatchType && clientKeywords) {
      const matched = rowMatchedKeywords(r);
      const set = extra.keywordMatchType === "individual" ? clientKeywords.nameKeywords : clientKeywords.domainKeywords;
      if (!matched.some((k) => set.has(k))) return false;
    }

    if (extra.searchQuery.trim()) {
      const q = extra.searchQuery.toLowerCase();
      const nameMatch = profileName(r).toLowerCase().includes(q);
      const urlMatch = (r.url || "").toLowerCase().includes(q);
      if (!nameMatch && !urlMatch) return false;
    }

    return true;
  });
}

/** The number the Analysis table actually SHOWS in its Risk column.
 *
 * There are two separate risk rubrics, deliberately (see
 * backend/shared/models/scoring.py vs incident_scoring.py): `risk_score` is
 * the tool's internal 2-9 triage score, `incident.riskRating` is the
 * client-facing rating that gets published. The table renders the latter --
 * so sorting on the former produced a Risk column that visibly wasn't
 * sorted. Sort on what is displayed, and fall back to the internal score
 * only for a row with no incident preview (a missing client record). */
function displayedRisk(r: Profile): number {
  const rating = r.incident?.riskRating;
  const parsed = rating === undefined || rating === null ? NaN : Number(rating);
  return Number.isFinite(parsed) ? parsed : r.risk_score || 0;
}

/** "recent" = highest risk first, "past" = lowest first for Analysis.
 * Discovery is left exactly as the server returned it -- deliberately no
 * client-side re-sort at all. That server order is the same order Facebook
 * itself rendered the result (see profile_repository.py::find, ascending
 * _id == insertion order == the order each platform actually returned
 * results; and backend/platforms/facebook/discovery_engine.py::sweep,
 * which now preserves true page-by-page order rather than a per-page-local
 * rank). Re-sorting here by a relevance heuristic -- which this function
 * used to do -- meant the UI never actually showed "what a real user sees
 * searching Facebook themselves", regardless of how faithfully the backend
 * scraped and stored that order; the card list just imposed its own
 * ordering on top of it. Does not mutate the input array. */
export function sortResults(
  rows: Profile[],
  order: SortOrder,
  phase?: string,
  _activeKeyword?: string,
  _status?: string,
): Profile[] {
  if (phase === "discovery") return rows;
  return [...rows].sort((a, b) =>
    order === "recent"
      ? displayedRisk(b) - displayedRisk(a)
      : displayedRisk(a) - displayedRisk(b),
  );
}

export function applyFilters(
  rows: Profile[],
  filters: ResultFilters,
  extra: ExtraFilters,
  order: SortOrder,
  platform?: string,
  clientKeywords?: ClientKeywordSets,
): Profile[] {
  return sortResults(
    filterResults(rows, filters, extra, platform, clientKeywords),
    order,
    filters.phase,
    extra.keywordFilter,
  );
}

// Mirrors backend/shared/models/incident_scoring.py::compute_incident_risk_score
// exactly (additive scale, logo floor of 6) plus incident_publisher.py's
// `_is_active` (last post within ACTIVE_WINDOW_DAYS) -- used to recompute
// the analysis table's Risk badge the instant an analyst toggles Username
// Match or Logo Match, rather than waiting on a round trip or the 3s poll
// for the server's own recompute to show up. The PATCH response still wins
// once it lands (see ResultsGrid.tsx::saveProfileField), so this is purely
// an instant preview, never the source of truth.
const ACTIVE_WINDOW_DAYS = 183;

function isRecentIso(iso: string | null | undefined, days: number): boolean {
  if (!iso) return false;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return false;
  return (Date.now() - t) / 86400000 < days;
}

export function computeIncidentRiskScorePreview(input: {
  logoMatch: boolean;
  usernameMatch: boolean;
  followers: number | null | undefined;
  location: string | null | undefined;
  lastPostDate: string | null | undefined;
}): number {
  const isActive = isRecentIso(input.lastPostDate, ACTIVE_WINDOW_DAYS);

  // 1. Active account with both Logo and Username match -> 9
  if (isActive && input.logoMatch && input.usernameMatch) {
    return 9;
  }

  // 2. Inactive account with both Logo and Username match + Location -> 8
  if (!isActive && input.logoMatch && input.usernameMatch && !!input.location) {
    return 8;
  }

  // 3. Inactive account with both Logo and Username match, no location -> 7
  if (input.logoMatch && input.usernameMatch) {
    return 7;
  }

  // 4. Partial match (either Logo OR Username match)
  if (input.logoMatch || input.usernameMatch) {
    const hasMetadata = isActive || !!input.location || (input.followers !== null && input.followers !== undefined) || !!input.lastPostDate;
    return hasMetadata ? 4 : 3;
  }

  // 5. Neither logo nor username match -> 2
  return 2;
}

// Telegram has no location concept at all for users/channels/groups, and
// Instagram's public profile schema has no structured location field either
// (only free-text bio) -- both permanent platform limitations.
export const NOT_EXPOSED: Partial<
  Record<"followers" | "location" | "last_post_date", Set<string>>
> = {
  location: new Set(["telegram", "instagram"]),
};

/** "how long has this been sitting here" -- the card's age badge. */
export function ageLabel(iso?: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 86400 * 30) return `${Math.floor(seconds / 86400)}d ago`;
  if (seconds < 86400 * 365) return `${Math.floor(seconds / (86400 * 30))}mo ago`;
  return `${Math.floor(seconds / (86400 * 365))}y ago`;
}

// An analysis outcome meaning the profile was never actually read, so an
// empty field says nothing about the profile. Mirrors the backend's
// RETRYABLE_ANALYSIS_STATUSES.
const NEVER_READ = new Set(["ERROR", "CHECKPOINT", "LOGIN_REQUIRED"]);

export function analysisWasBlocked(r: Profile): boolean {
  return !!r.analysis_status && NEVER_READ.has(r.analysis_status);
}

export function emptyLabel(
  r: Profile,
  platform: string,
  field: "followers" | "location" | "last_post_date",
): string {
  if (r.phase === "discovery") return "not analysed yet";
  // A blocked run is the reason this cell is empty -- saying "not exposed
  // by this platform" there would blame the platform for a session problem
  // and read as a settled fact about the profile.
  if (analysisWasBlocked(r)) return "analysis could not read this profile";
  if (NOT_EXPOSED[field]?.has(platform)) {
    return "not exposed by this platform";
  }
  return "—";
}
