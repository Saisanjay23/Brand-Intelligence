import { describe, expect, it } from "vitest";
import type { Job, Profile } from "../api/types";
import {
  ageLabel,
  emptyLabel,
  estimateRemainingSeconds,
  filterResults,
  formatEta,
  keywordRelevance,
  sortResults,
  type ExtraFilters,
  type ResultFilters,
} from "./resultsFilter";

let counter = 0;
function makeProfile(overrides: Partial<Profile> = {}): Profile {
  counter += 1;
  return {
    id: `id-${counter}`,
    platform: "facebook",
    url: `https://example.com/${counter}`,
    status: "pending",
    has_logo: false,
    phase: "discovery",
    profile_name: `Profile ${counter}`,
    risk_score: 2,
    priority: "Low",
    followers: null,
    ...overrides,
  };
}

const NO_FILTERS: ResultFilters = { status: "", priority: "", phase: "" };
const NO_EXTRA: ExtraFilters = { keywordFilter: "", searchQuery: "" };

describe("filterResults", () => {
  it("passes everything through when no filter is set", () => {
    const rows = [makeProfile(), makeProfile(), makeProfile()];
    expect(filterResults(rows, NO_FILTERS, NO_EXTRA)).toHaveLength(3);
  });

  it("filters by status", () => {
    const rows = [
      makeProfile({ status: "approved" }),
      makeProfile({ status: "pending" }),
      makeProfile({ status: "rejected" }),
    ];
    const out = filterResults(rows, { ...NO_FILTERS, status: "approved" }, NO_EXTRA);
    expect(out).toHaveLength(1);
    expect(out[0].status).toBe("approved");
  });

  it("filters by priority", () => {
    const rows = [
      makeProfile({ priority: "High" }),
      makeProfile({ priority: "Medium" }),
      makeProfile({ priority: "Low" }),
    ];
    const out = filterResults(rows, { ...NO_FILTERS, priority: "High" }, NO_EXTRA);
    expect(out).toHaveLength(1);
    expect(out[0].priority).toBe("High");
  });

  it("filters by phase -- the discovery/analysis tab split", () => {
    const rows = [
      makeProfile({ phase: "discovery" }),
      makeProfile({ phase: "analysis" }),
      makeProfile({ phase: "analysis" }),
    ];
    const discovery = filterResults(rows, { ...NO_FILTERS, phase: "discovery" }, NO_EXTRA);
    const analysis = filterResults(rows, { ...NO_FILTERS, phase: "analysis" }, NO_EXTRA);
    expect(discovery).toHaveLength(1);
    expect(analysis).toHaveLength(2);
  });

  it("an approved profile still shows up under the discovery phase filter", () => {
    // approved-but-not-yet-analysed stays phase="discovery" server-side
    // until analysis actually writes scored fields onto it
    const rows = [makeProfile({ phase: "discovery", status: "approved" })];
    const out = filterResults(rows, { ...NO_FILTERS, phase: "discovery" }, NO_EXTRA);
    expect(out).toHaveLength(1);
  });

  it("applies status, priority and phase together as AND, not OR", () => {
    const target = makeProfile({ status: "approved", priority: "High", phase: "analysis" });
    const rows = [
      target,
      makeProfile({ status: "approved", priority: "High", phase: "discovery" }),
      makeProfile({ status: "approved", priority: "Low", phase: "analysis" }),
      makeProfile({ status: "pending", priority: "High", phase: "analysis" }),
    ];
    const out = filterResults(
      rows,
      { status: "approved", priority: "High", phase: "analysis" },
      NO_EXTRA,
    );
    expect(out).toEqual([target]);
  });

  it("keyword filter is a case-insensitive substring match on the analysis-phase keyword field", () => {
    const rows = [
      makeProfile({ phase: "analysis", keyword: "adani power" }),
      makeProfile({ phase: "analysis", keyword: "adani retail" }),
      makeProfile({ phase: "analysis", keyword: undefined }),
    ];
    const out = filterResults(rows, NO_FILTERS, { ...NO_EXTRA, keywordFilter: "power" });
    expect(out).toHaveLength(1);
    expect(out[0].keyword).toBe("adani power");
  });

  it("search query matches profile_name or username, case-insensitively", () => {
    const rows = [
      makeProfile({ profile_name: "Gautam Adani" }),
      makeProfile({ profile_name: "Someone Else" }),
      makeProfile({ profile_name: undefined, username: "adani_group" }),
    ];
    const out = filterResults(rows, NO_FILTERS, { ...NO_EXTRA, searchQuery: "adani" });
    expect(out).toHaveLength(2);
  });

  it("search query falls back to url when no name is present", () => {
    const rows = [
      makeProfile({ profile_name: undefined, username: undefined, url: "https://x.com/adanigroup" }),
      makeProfile({ profile_name: undefined, username: undefined, url: "https://x.com/nope" }),
    ];
    const out = filterResults(rows, NO_FILTERS, { ...NO_EXTRA, searchQuery: "adani" });
    expect(out).toHaveLength(1);
  });

  it("blank search query (whitespace only) matches everything", () => {
    const rows = [makeProfile(), makeProfile()];
    const out = filterResults(rows, NO_FILTERS, { ...NO_EXTRA, searchQuery: "   " });
    expect(out).toHaveLength(2);
  });

  it("filters by platform", () => {
    const rows = [makeProfile({ platform: "facebook" }), makeProfile({ platform: "instagram" })];
    const out = filterResults(rows, NO_FILTERS, NO_EXTRA, "instagram");
    expect(out).toHaveLength(1);
    expect(out[0].platform).toBe("instagram");
  });
});

describe("sortResults", () => {
  it("'recent' orders highest risk score first", () => {
    const rows = [
      makeProfile({ risk_score: 3 }),
      makeProfile({ risk_score: 9 }),
      makeProfile({ risk_score: 5 }),
    ];
    const out = sortResults(rows, "recent");
    expect(out.map((r) => r.risk_score)).toEqual([9, 5, 3]);
  });

  it("'past' orders lowest risk score first", () => {
    const rows = [
      makeProfile({ risk_score: 3 }),
      makeProfile({ risk_score: 9 }),
      makeProfile({ risk_score: 5 }),
    ];
    const out = sortResults(rows, "past");
    expect(out.map((r) => r.risk_score)).toEqual([3, 5, 9]);
  });

  it("does not mutate the input array", () => {
    const rows = [makeProfile({ risk_score: 3 }), makeProfile({ risk_score: 9 })];
    const original = [...rows];
    sortResults(rows, "recent");
    expect(rows).toEqual(original);
  });

  // The analysis table renders `incident.riskRating` (the client-facing
  // rubric), not `risk_score` (the internal triage rubric). Sorting on the
  // internal one produced a visibly unsorted Risk column.
  it("sorts analysis rows by the riskRating the table actually displays", () => {
    const withRating = (rating: string, internal: number) =>
      makeProfile({
        phase: "analysis",
        risk_score: internal,
        incident: { riskRating: rating } as Profile["incident"],
      });
    // internal scores deliberately ordered OPPOSITE to the displayed rating,
    // so sorting on the wrong field is unambiguous
    const rows = [withRating("4", 9), withRating("9", 2), withRating("7", 5)];
    const out = sortResults(rows, "recent", "analysis");
    expect(out.map((r) => r.incident?.riskRating)).toEqual(["9", "7", "4"]);
  });

  it("falls back to the internal score when a row has no incident preview", () => {
    // happens when the client record is gone -- build_incident_doc returns
    // null there, so there is no riskRating to sort on
    const rows = [
      makeProfile({ phase: "analysis", risk_score: 3 }),
      makeProfile({ phase: "analysis", risk_score: 9 }),
    ];
    const out = sortResults(rows, "recent", "analysis");
    expect(out.map((r) => r.risk_score)).toEqual([9, 3]);
  });
});

describe("emptyLabel blames the right thing", () => {
  // A blocked run and a platform that doesn't expose a field produce the
  // same empty cell for completely different reasons, and an analyst acts
  // differently on each.
  it("says analysis could not read the profile when the run was blocked", () => {
    for (const reason of ["ERROR", "CHECKPOINT", "LOGIN_REQUIRED"]) {
      const row = makeProfile({ phase: "analysis", analysis_status: reason });
      expect(emptyLabel(row, "facebook", "followers")).toBe(
        "analysis could not read this profile",
      );
    }
  });

  it("does not blame the session when the profile was genuinely read", () => {
    const row = makeProfile({ phase: "analysis", analysis_status: "OK" });
    expect(emptyLabel(row, "telegram", "location")).toBe("not exposed by this platform");
  });

  it("a blocked run outranks the platform-limitation label", () => {
    // Telegram genuinely has no location -- but if the run never got in,
    // that is the reason this cell is empty, not the platform.
    const row = makeProfile({ phase: "analysis", analysis_status: "CHECKPOINT" });
    expect(emptyLabel(row, "telegram", "location")).toBe(
      "analysis could not read this profile",
    );
  });
});

describe("emptyLabel", () => {
  it("says 'not analysed yet' for discovery-phase rows regardless of platform", () => {
    const row = makeProfile({ phase: "discovery" });
    expect(emptyLabel(row, "twitter", "followers")).toBe("not analysed yet");
    expect(emptyLabel(row, "telegram", "location")).toBe("not analysed yet");
  });

  it("says 'not exposed by this platform' for Telegram location -- a genuine platform limitation", () => {
    const row = makeProfile({ phase: "analysis" });
    expect(emptyLabel(row, "telegram", "location")).toBe("not exposed by this platform");
  });

  it("says 'not exposed by this platform' for Instagram location too", () => {
    const row = makeProfile({ phase: "analysis" });
    expect(emptyLabel(row, "instagram", "location")).toBe("not exposed by this platform");
  });

  it("falls back to a plain dash for an ordinary analysed-but-empty field", () => {
    const row = makeProfile({ phase: "analysis" });
    expect(emptyLabel(row, "facebook", "location")).toBe("—");
    expect(emptyLabel(row, "youtube", "followers")).toBe("—");
  });
});

describe("ageLabel", () => {
  const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();

  it("returns '' for a missing timestamp", () => {
    expect(ageLabel(undefined)).toBe("");
  });

  it("returns '' for an unparseable timestamp", () => {
    expect(ageLabel("not-a-date")).toBe("");
  });

  it("reads 'just now' under a minute", () => {
    expect(ageLabel(iso(30 * 1000))).toBe("just now");
  });

  it("steps through minutes, hours, days, months, years", () => {
    expect(ageLabel(iso(5 * 60 * 1000))).toBe("5m ago");
    expect(ageLabel(iso(3 * 3600 * 1000))).toBe("3h ago");
    expect(ageLabel(iso(2 * 86400 * 1000))).toBe("2d ago");
    expect(ageLabel(iso(2 * 86400 * 30 * 1000))).toBe("2mo ago");
    expect(ageLabel(iso(2 * 86400 * 365 * 1000))).toBe("2y ago");
  });
});

describe("keywordRelevance / discovery sort", () => {
  it("ranks an exact name match closer than a partial one", () => {
    const exact = makeProfile({ profile_name: "Tesla", keyword: "Tesla" });
    const partial = makeProfile({ profile_name: "Tesla Fan Club 2019", keyword: "Tesla" });
    expect(keywordRelevance(exact, "Tesla")).toBeLessThan(keywordRelevance(partial, "Tesla"));
  });

  it("ranks a blank/unrelated name worst", () => {
    const blank = makeProfile({ profile_name: "", keyword: "Tesla" });
    const unrelated = makeProfile({ profile_name: "Completely Unrelated Name", keyword: "Tesla" });
    const partial = makeProfile({ profile_name: "Tesla Motors Fan", keyword: "Tesla" });
    expect(keywordRelevance(partial, "Tesla")).toBeLessThan(keywordRelevance(unrelated, "Tesla"));
    expect(keywordRelevance(unrelated, "Tesla")).toBeLessThanOrEqual(keywordRelevance(blank, "Tesla"));
  });

  it("is case-insensitive for an exact match", () => {
    const r = makeProfile({ profile_name: "TESLA", keyword: "tesla" });
    expect(keywordRelevance(r, "tesla")).toBe(0);
  });

  it("strips punctuation before comparing, so 'TESLA, Inc.' is a prefix match not a miss", () => {
    const r = makeProfile({ profile_name: "TESLA, Inc.", keyword: "tesla" });
    const score = keywordRelevance(r, "tesla");
    expect(score).toBeGreaterThan(0);
    expect(score).toBeLessThan(50);
  });

  it("falls back to the row's own keyword when no active filter is given", () => {
    const r = makeProfile({ profile_name: "Tesla", keyword: "Tesla" });
    expect(keywordRelevance(r)).toBe(0);
  });

  it("sortResults leaves discovery rows in exactly the order the server returned them", () => {
    // Discovery deliberately does NOT re-sort by relevance (that's what
    // keywordRelevance's own tests above cover as a standalone utility) --
    // the server's order already IS Facebook's own top-to-bottom render
    // order (see profile_repository.py::find + the Facebook discovery
    // engine's sweep() ordering fix), and the whole point of this view is
    // showing an analyst exactly that, unmodified.
    const rows = [
      makeProfile({ profile_name: "", keyword: "Tesla", risk_score: 5 }),
      makeProfile({ profile_name: "Tesla Fan Club 2019", keyword: "Tesla", risk_score: 9 }),
      makeProfile({ profile_name: "Tesla", keyword: "Tesla", risk_score: 0 }),
    ];
    const out = sortResults(rows, "recent", "discovery", "Tesla");
    expect(out).toEqual(rows);
  });

  it("analysis phase keeps the existing risk-score sort untouched", () => {
    const rows = [
      makeProfile({ profile_name: "Tesla", risk_score: 2 }),
      makeProfile({ profile_name: "Zzz Unrelated", risk_score: 9 }),
    ];
    const out = sortResults(rows, "recent", "analysis", "Tesla");
    expect(out[0].risk_score).toBe(9);
  });
});

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "j1",
    kind: "discovery",
    platform: null,
    client_id: "acme",
    params: {},
    status: "running",
    message: "",
    found: 0,
    total: 0,
    new_profiles: 0,
    error: "",
    started: new Date().toISOString(),
    finished: "",
    last_seq: 0,
    platforms: {},
    ...overrides,
  };
}

describe("estimateRemainingSeconds -- discovery (time-budget ceiling)", () => {
  it("returns the full budget when the job just started", () => {
    const job = makeJob({
      kind: "discovery",
      started: new Date().toISOString(),
      params: { keywords: ["tesla"], tabs: ["people"], max_seconds: 300, concurrency: 1 },
    });
    const eta = estimateRemainingSeconds(job)!;
    expect(eta.ceiling).toBe(true);
    expect(eta.seconds).toBeGreaterThan(295);
    expect(eta.seconds).toBeLessThanOrEqual(300);
  });

  it("accounts for multiple sweeps sharing limited concurrency", () => {
    const job = makeJob({
      kind: "discovery",
      started: new Date().toISOString(),
      params: {
        keywords: ["a", "b", "c", "d"],
        tabs: ["people", "pages"],
        max_seconds: 100,
        concurrency: 2,
      },
    });
    const eta = estimateRemainingSeconds(job)!;
    expect(eta.seconds).toBeGreaterThan(395);
    expect(eta.seconds).toBeLessThanOrEqual(400);
  });

  it("shrinks as elapsed time grows", () => {
    const startedAgo = new Date(Date.now() - 120_000).toISOString();
    const job = makeJob({
      kind: "discovery",
      started: startedAgo,
      params: { keywords: ["tesla"], tabs: ["people"], max_seconds: 300, concurrency: 1 },
    });
    const eta = estimateRemainingSeconds(job)!;
    expect(eta.seconds).toBeGreaterThan(170);
    expect(eta.seconds).toBeLessThan(190);
  });

  it("returns null when max_seconds is missing (nothing to bound against)", () => {
    const job = makeJob({ kind: "discovery", params: { keywords: ["tesla"] } });
    expect(estimateRemainingSeconds(job)).toBeNull();
  });
});

describe("estimateRemainingSeconds -- analysis (rate extrapolation)", () => {
  it("extrapolates remaining time from the observed found/elapsed rate", () => {
    const startedAgo = new Date(Date.now() - 60_000).toISOString();
    const job = makeJob({ kind: "analysis", started: startedAgo, found: 10, total: 30 });
    const eta = estimateRemainingSeconds(job)!;
    expect(eta.ceiling).toBe(false);
    expect(eta.seconds).toBeGreaterThan(110);
    expect(eta.seconds).toBeLessThan(130);
  });

  it("returns 0 once found reaches total", () => {
    const job = makeJob({ kind: "analysis", found: 30, total: 30 });
    expect(estimateRemainingSeconds(job)).toEqual({ seconds: 0, ceiling: false });
  });

  it("returns null before the first item -- this backend echoes no per-profile pacing delay to extrapolate a ceiling from", () => {
    const job = makeJob({ kind: "analysis", found: 0, total: 30 });
    expect(estimateRemainingSeconds(job)).toBeNull();
  });
});

describe("formatEta", () => {
  it("formats a discovery ceiling estimate with the 'up to' prefix", () => {
    expect(formatEta({ seconds: 125, ceiling: true })).toBe("up to 3m left");
    expect(formatEta({ seconds: 45, ceiling: true })).toBe("up to 45s left");
  });

  it("formats an analysis rate estimate with the '~' prefix", () => {
    expect(formatEta({ seconds: 125, ceiling: false })).toBe("~3m left");
  });

  it("returns an empty string when there is nothing to estimate", () => {
    expect(formatEta(null)).toBe("");
  });
});
