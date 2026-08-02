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

/** One unified filter pass over the rows already in memory -- no tabs, no
 * network round trip per toggle. Every predicate is independent (AND logic). */
export function filterResults(
  rows: Profile[],
  filters: ResultFilters,
  extra: ExtraFilters,
  platform?: string,
): Profile[] {
  return rows.filter((r) => {
    if (platform && platform !== "all" && r.platform !== platform) return false;
    if (filters.phase !== "analysis" && filters.status && r.status !== filters.status) return false;
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

    if (extra.searchQuery.trim()) {
      const q = extra.searchQuery.toLowerCase();
      const nameMatch = profileName(r).toLowerCase().includes(q);
      const urlMatch = (r.url || "").toLowerCase().includes(q);
      if (!nameMatch && !urlMatch) return false;
    }

    return true;
  });
}

/** "recent" = highest risk first, "past" = lowest first. Does not mutate the
 * input array. `phase`/`activeKeyword` are only meaningful for Discovery:
 * that's the one view with no risk score to sort by, so it sorts by keyword
 * relevance instead. */
export function sortResults(
  rows: Profile[],
  order: SortOrder,
  phase?: string,
  activeKeyword?: string,
): Profile[] {
  if (phase === "discovery") {
    return [...rows].sort((a, b) => {
      const ra = keywordRelevance(a, activeKeyword);
      const rb = keywordRelevance(b, activeKeyword);
      if (ra !== rb) return ra - rb;
      if (a.has_logo !== b.has_logo) return a.has_logo ? -1 : 1;
      return 0;
    });
  }
  return [...rows].sort((a, b) =>
    order === "recent"
      ? (b.risk_score || 0) - (a.risk_score || 0)
      : (a.risk_score || 0) - (b.risk_score || 0),
  );
}

export function applyFilters(
  rows: Profile[],
  filters: ResultFilters,
  extra: ExtraFilters,
  order: SortOrder,
  platform?: string,
): Profile[] {
  return sortResults(
    filterResults(rows, filters, extra, platform),
    order,
    filters.phase,
    extra.keywordFilter,
  );
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

export function emptyLabel(
  r: Profile,
  platform: string,
  field: "followers" | "location" | "last_post_date",
): string {
  if (r.phase === "discovery") return "not analysed yet";
  if (NOT_EXPOSED[field]?.has(platform)) {
    return "not exposed by this platform";
  }
  return "—";
}
