import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { profilesApi } from "../api/profilesApi";
import type { JobEvent, PlatformHealth, PlatformProgress, Profile, Status } from "../api/types";
import { PlatformIcon } from "./PlatformIcon";
import {
  emptyLabel,
  filterResults,
  sortResults,
  type ExtraFilters,
  type ResultFilters,
} from "../services/resultsFilter";
import { download } from "../utils/download";

interface Props {
  clientId: string;
  platforms: PlatformHealth[];
  discoveryRunning: boolean;
  discoveryLog: JobEvent[];
  discoveryProgress: Record<string, PlatformProgress>;
  analysisRunning: boolean;
  analysisLog: JobEvent[];
  analysisProgress: Record<string, PlatformProgress>;
  onError?: (msg: string) => void;
}

const PAGE_SIZE = 25;
const EXPORT_LIMIT = 5000;

// "5s" / "2m 30s" / "1h 5m" -- never both units at zero, never blank.
function formatEta(seconds: number | null): string {
  if (seconds === null || seconds < 0) return "";
  if (seconds < 5) return "almost done";
  if (seconds < 60) return `~${Math.round(seconds)}s left`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  if (mins < 60) return `~${mins}m${secs ? ` ${secs}s` : ""} left`;
  const hrs = Math.floor(mins / 60);
  return `~${hrs}h ${mins % 60}m left`;
}

const PLATFORM_STATUS_LOOK: Record<PlatformProgress["status"], { icon: string; color: string }> = {
  pending: { icon: "⏳", color: "var(--text-dim)" },
  running: { icon: "⚙️", color: "var(--cyan)" },
  done: { icon: "✅", color: "var(--success)" },
  failed: { icon: "⚠️", color: "var(--danger)" },
};

function PlatformProgressRow({ label, progress }: { label: string; progress: PlatformProgress }) {
  const look = PLATFORM_STATUS_LOOK[progress.status];
  const pct = progress.total > 0 ? Math.min(100, Math.round((progress.processed / progress.total) * 100)) : 0;
  return (
    <div style={{ marginTop: "4px" }}>
      <div
        style={{
          display: "flex", alignItems: "center", gap: "5px",
          fontSize: "10px", fontFamily: "var(--font-mono)", color: look.color,
        }}
      >
        <span>{look.icon}</span>
        <span>{label}</span>
        <span style={{ flex: 1 }} />
        <span>
          {progress.processed}/{progress.total || "?"}
        </span>
      </div>
      {progress.status === "running" && (
        <>
          <div
            style={{
              height: "3px", background: "var(--bg-inner)", borderRadius: "999px",
              overflow: "hidden", marginTop: "3px",
            }}
          >
            <div
              style={{
                height: "100%", width: `${pct || 4}%`,
                background: "linear-gradient(90deg, var(--cyan), var(--purple))",
                transition: "width 0.4s ease",
              }}
            />
          </div>
          {progress.eta_seconds !== null && (
            <div style={{ fontSize: "9px", color: "var(--text-dim)", marginTop: "2px" }}>
              {formatEta(progress.eta_seconds)}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function LiveFeed({ title, log }: { title: string; log: JobEvent[] }) {
  return (
    <details className="dashboard-card-box" style={{ marginTop: "12px" }} open>
      <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: "12px" }}>
        {title} ({log.length})
      </summary>
      <ul
        style={{
          listStyle: "none",
          padding: 0,
          marginTop: "8px",
          maxHeight: "160px",
          overflowY: "auto",
          fontFamily: "var(--font-mono)",
          fontSize: "11px",
        }}
      >
        {log.slice(-30).map((e, i) => (
          <li key={i} style={{ padding: "3px 0", display: "flex", gap: "8px" }}>
            <span style={{ color: "var(--cyan)" }}>[{e.type}]</span>
            <span style={{ color: "var(--text-main)" }}>{e.message}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

// A freshly analysed row is held back from the client-facing view for a
// review window (see backend/docs/adr/0007-publish-hold.md) -- this is the
// countdown shown to the analyst, recomputed on every render rather than a
// ticking interval, since minute-level precision is all a review workflow
// needs.
function holdRemainingLabel(publishHoldUntil?: string | null): string {
  if (!publishHoldUntil) return "";
  const remainingMs = new Date(publishHoldUntil).getTime() - Date.now();
  if (remainingMs <= 0) return "publishing…";
  const mins = Math.ceil(remainingMs / 60000);
  return mins <= 1 ? "~1m left" : `~${mins}m left`;
}

function toCsv(rows: Profile[]): string {
  const cols = [
    "id", "platform", "status", "phase", "url", "profile_name", "username",
    "risk_score", "priority", "followers", "location", "last_post_date", "keyword", "comments",
  ] as const;
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [cols.join(",")];
  for (const r of rows) lines.push(cols.map((c) => esc(r[c])).join(","));
  return lines.join("\n");
}

function ProfileAvatar({ r, size }: { r: Profile; size?: number }) {
  const [error, setError] = useState(false);

  useEffect(() => {
    setError(false);
  }, [r.profile_image_url]);

  if (!r.profile_image_url || error) {
    return (
      <span
        className="profile-avatar-circle"
        style={size ? { width: size, height: size, fontSize: size * 0.45, borderRadius: "50%" } : undefined}
      >
        {(r.profile_name || r.username || "?").charAt(0).toUpperCase()}
      </span>
    );
  }
  const src = r.profile_image_url.startsWith("http")
    ? `/profiles/media-proxy?url=${encodeURIComponent(r.profile_image_url)}`
    : r.profile_image_url;
  return (
    <img
      src={src}
      alt=""
      referrerPolicy="no-referrer"
      loading="lazy"
      style={size ? { width: size, height: size, borderRadius: "50%", objectFit: "cover" } : { width: "100%", height: "100%", objectFit: "cover" }}
      onError={() => setError(true)}
    />
  );
}

interface CardProps {
  r: Profile;
  isAnalysisView: boolean;
  savingId: string | null;
  onDecide: (id: string, next: Status) => void;
  onPublish: (id: string) => void;
  onValidate: (id: string, logoMatch: boolean, usernameMatch: boolean) => void;
}

// Mirrors backend shared/models/scoring.py::NAME_THRESHOLD.
const MATCH_HIGH_THRESHOLD = 80;

function ProfileCard({ r, isAnalysisView, savingId, onDecide, onPublish, onValidate }: CardProps) {
  const name = r.profile_name || r.username || r.url;
  const isHeld = isAnalysisView && r.published === false;
  const isDiscovery = !isAnalysisView;
  const [logoMatch, setLogoMatch] = useState(r.logo_match ?? false);
  const [usernameMatch, setUsernameMatch] = useState(r.username_match ?? false);

  useEffect(() => {
    setLogoMatch(r.logo_match ?? false);
    setUsernameMatch(r.username_match ?? false);
  }, [r.id, r.logo_match, r.username_match]);

  return (
    <div className="profile-card">
      <div className="profile-card-header">
        <ProfileAvatar r={r} />
        <span
          className="card-badge-top-left"
          style={{
            background: r.status === "approved" ? "rgba(0,193,77,0.85)" : r.status === "rejected" ? "rgba(233,80,83,0.85)" : "rgba(136,56,221,0.85)",
            color: "#fff",
          }}
        >
          {r.status}
        </span>
        {isAnalysisView && r.priority && (
          <span
            className="card-badge-top-right"
            style={{
              background: r.priority === "High" ? "rgba(233,80,83,0.85)" : r.priority === "Medium" ? "rgba(255,128,0,0.85)" : "rgba(102,112,133,0.85)",
              color: "#fff",
            }}
          >
            {r.priority}
          </span>
        )}
        {isDiscovery && r.name_score !== null && r.name_score !== undefined && (
          <span
            className="card-badge-top-right"
            title={`Name-to-keyword match score: ${r.name_score}/100`}
            style={{
              background: r.name_score >= MATCH_HIGH_THRESHOLD ? "rgba(0,193,77,0.85)" : "rgba(255,128,0,0.85)",
              color: "#fff",
            }}
          >
            {r.name_score >= MATCH_HIGH_THRESHOLD ? "🎯 High Match" : "🎯 Low Match"}
          </span>
        )}
        <span className="card-badge-platform">
          <PlatformIcon platform={r.platform} size={14} />
          {r.platform}
        </span>
      </div>
      <div className="profile-card-body">
        <div className="profile-name-row">
          <a href={r.url} target="_blank" rel="noreferrer" className="profile-display-name" style={{ color: "var(--text-main)" }}>
            {name}
          </a>
          {r.verified && (
            <span className="verified-check" title="Verified account on this platform">
              ✓
            </span>
          )}
        </div>
        {isAnalysisView && r.username && <div className="profile-handle">@{r.username}</div>}
        {isDiscovery && !!r.keywords?.length && (
          <div className="card-keyword-tags">
            {r.keywords.map((kw) => (
              <span key={kw} className="card-keyword-tag">
                🔑 {kw}
              </span>
            ))}
          </div>
        )}

        {isHeld && (
          <div
            style={{
              fontSize: "11px", color: "var(--purple)", background: "rgba(136,56,221,0.1)",
              border: "1px solid rgba(136,56,221,0.3)", borderRadius: "6px",
              padding: "4px 8px", marginTop: "4px",
            }}
            title="Not yet visible outside this tool -- gives you a window to revert a false positive before it's published"
          >
            ⏳ On hold — {holdRemainingLabel(r.publish_hold_until)}
          </div>
        )}

        {isAnalysisView && (
          <div className="card-detail-row">
            <span>👥 {r.followers ?? emptyLabel(r, r.platform, "followers")}</span>
            <span>📍 {r.location || emptyLabel(r, r.platform, "location")}</span>
            <span>🕐 {r.last_post_date || emptyLabel(r, r.platform, "last_post_date")}</span>
          </div>
        )}

        {(r.logo_match || r.username_match) && (
          <div className="card-detail-row">
            {r.logo_match && <span>🖼️ Logo match</span>}
            {r.username_match && <span>🔖 Username match</span>}
          </div>
        )}

        <div className="card-meta-row">
          <span style={{ fontSize: "11px", color: "var(--text-dim)" }}>
            {isAnalysisView ? `Risk ${r.risk_score ?? "—"}` : r.comments || ""}
          </span>
        </div>

        {isDiscovery && r.status !== "approved" && r.status !== "rejected" && (
          <div className="card-validate-row" title="Tick what you visually confirmed matches the brand, then Validate">
            <label className="card-validate-check">
              <input type="checkbox" checked={logoMatch} onChange={(e) => setLogoMatch(e.target.checked)} />
              Logo match
            </label>
            <label className="card-validate-check">
              <input type="checkbox" checked={usernameMatch} onChange={(e) => setUsernameMatch(e.target.checked)} />
              Username match
            </label>
          </div>
        )}

        <div className="card-actions-row">
          {isDiscovery && r.status !== "approved" && (
            <button
              className="btn-accept"
              disabled={savingId === r.id}
              onClick={() => onValidate(r.id, logoMatch, usernameMatch)}
              title="Approves this profile and records the logo/username match confirmation, carried through to analysis"
            >
              ✅ Validate
            </button>
          )}
          {/* Discovery cards use Validate (above) instead of a plain Approve
              -- it captures the logo/username match confirmation the same
              action would otherwise skip. Analysis cards have no Validate
              alternative, so they keep the plain Approve. */}
          {!isDiscovery && r.status !== "approved" && (
            <button className="btn-accept" disabled={savingId === r.id} onClick={() => onDecide(r.id, "approved")}>
              ✓ Approve
            </button>
          )}
          {r.status !== "rejected" && (
            <button className="btn-reject" disabled={savingId === r.id} onClick={() => onDecide(r.id, "rejected")}>
              ✕ Reject
            </button>
          )}
          {isHeld && (
            <button
              className="btn-accept"
              disabled={savingId === r.id}
              onClick={() => onPublish(r.id)}
              title="Skip the rest of the hold and publish this result now"
            >
              📢 Publish Now
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function ResultsGrid({
  clientId,
  platforms,
  discoveryRunning,
  discoveryLog,
  discoveryProgress,
  analysisRunning,
  analysisLog,
  analysisProgress,
  onError,
}: Props) {
  const [platform, setPlatform] = useState("");
  const [phase, setPhase] = useState<"discovery" | "analysis">("discovery");
  const [status, setStatus] = useState("pending");
  const [priority, setPriority] = useState("");
  const [sortOrder, setSortOrder] = useState<"recent" | "past">("recent");
  const [keywordFilter, setKeywordFilter] = useState("");
  const [matchLevel, setMatchLevel] = useState<"" | "high" | "low">("");
  const [entityType, setEntityType] = useState<"" | "profile" | "page">("");
  const [searchQuery, setSearchQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [viewMode, setViewMode] = useState<"grid" | "table">("grid");

  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<{ platforms: Record<string, number>; statuses: Record<string, number>; keywords: Record<string, number> }>({
    platforms: {},
    statuses: {},
    keywords: {},
  });
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [copyUrlState, setCopyUrlState] = useState<"idle" | "copied" | "failed">("idle");

  const isAnalysisView = phase === "analysis";

  // load() is called from a lot of places that can overlap in time: the
  // 3s live-poll while a job runs, and a fresh reload fired right after
  // every approve/reject/validate/publish. Those requests can resolve out
  // of order (a fast small query can land after a slower earlier one), and
  // without guarding against that, whichever response happens to arrive
  // LAST wins -- even if it's the stale one -- silently reverting a card
  // that had just been acted on and making the rest of the grid look like
  // it never moved. This ref tracks the most recently ISSUED request; a
  // response only gets applied to state if it's still the latest one by
  // the time it comes back, so a slow stale response is simply discarded.
  const requestSeq = useRef(0);

  const load = useCallback(
    async (showLoading = true) => {
      const seq = ++requestSeq.current;
      if (!clientId) {
        setProfiles([]);
        setTotal(0);
        setCounts({ platforms: {}, statuses: {}, keywords: {} });
        return;
      }
      if (showLoading) setLoading(true);
      try {
        const res = await profilesApi.profiles({
          client_id: clientId,
          platform: platform || undefined,
          status: !isAnalysisView && status ? status : undefined,
          phase,
          keyword: !isAnalysisView && keywordFilter ? keywordFilter : undefined,
          limit: PAGE_SIZE,
          offset,
        });
        if (seq !== requestSeq.current) return; // a newer load() has since been issued -- drop this stale response
        if (res.items.length === 0 && offset > 0 && res.total > 0) {
          setOffset(0);
          return;
        }
        setProfiles(res.items);
        setTotal(res.total);
        if (res.counts) {
          setCounts({
            platforms: res.counts.platforms || {},
            statuses: res.counts.statuses || {},
            keywords: res.counts.keywords || {},
          });
        }
      } catch (e) {
        if (seq === requestSeq.current) onError?.((e as Error).message);
      } finally {
        if (showLoading && seq === requestSeq.current) setLoading(false);
      }
    },
    [clientId, platform, status, phase, keywordFilter, isAnalysisView, offset, onError],
  );

  useEffect(() => {
    setOffset(0);
  }, [clientId, platform, status, phase, keywordFilter]);

  useEffect(() => {
    load(true);
  }, [load]);

  // Live preview polling while either engine runs, same cadence the old
  // WebSocket-driven view refreshed at -- this backend polls for progress
  // too now (see docs/adr/0002), so results polling matches that rhythm.
  useEffect(() => {
    if (!discoveryRunning && !analysisRunning) return;
    const interval = setInterval(() => load(false), 3000);
    return () => clearInterval(interval);
  }, [discoveryRunning, analysisRunning, load]);

  const isFacebook = platform === "facebook";
  const filters: ResultFilters = { status: !isAnalysisView ? status : "", priority, phase };
  const extra: ExtraFilters = {
    keywordFilter,
    searchQuery,
    matchLevel: !isAnalysisView ? matchLevel : "",
    entityType: !isAnalysisView && isFacebook ? entityType : "",
  };
  const displayed = useMemo(
    () => sortResults(filterResults(profiles, filters, extra, platform), sortOrder, phase, keywordFilter),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [profiles, status, priority, phase, keywordFilter, matchLevel, entityType, searchQuery, sortOrder, platform],
  );

  const decide = async (id: string, next: Status) => {
    const prev = profiles.find((r) => r.id === id);
    setProfiles((rows) => {
      const updated = rows.map((r) => (r.id === id ? { ...r, status: next } : r));
      return status ? updated.filter((r) => r.status === status) : updated;
    });
    setSavingId(id);
    try {
      await profilesApi.patchProfile(id, { status: next });
      // Automatically pull next items from page 2 into page 1 without needing manual navigation
      await load(false);
    } catch (e) {
      if (prev) setProfiles((rows) => [...rows.filter((r) => r.id !== prev.id), prev]);
      onError?.((e as Error).message);
    } finally {
      setSavingId(null);
    }
  };

  // Approves the profile the same way `decide` does, but also records the
  // analyst's own visual confirmation of a logo/username impersonation
  // match -- saved to the DB and carried through unchanged onto the
  // analysis-phase record (see backend/services/profile_service.py).
  const validate = async (id: string, logoMatch: boolean, usernameMatch: boolean) => {
    const prev = profiles.find((r) => r.id === id);
    setProfiles((rows) => {
      const updated = rows.map((r) =>
        r.id === id ? { ...r, status: "approved" as Status, logo_match: logoMatch, username_match: usernameMatch } : r,
      );
      return status ? updated.filter((r) => r.status === status) : updated;
    });
    setSavingId(id);
    try {
      await profilesApi.patchProfile(id, { status: "approved", logo_match: logoMatch, username_match: usernameMatch });
      await load(false);
    } catch (e) {
      if (prev) setProfiles((rows) => [...rows.filter((r) => r.id !== prev.id), prev]);
      onError?.((e as Error).message);
    } finally {
      setSavingId(null);
    }
  };

  const publish = async (id: string) => {
    const prev = profiles.find((r) => r.id === id);
    setProfiles((rows) => rows.map((r) => (r.id === id ? { ...r, published: true } : r)));
    setSavingId(id);
    try {
      await profilesApi.publishProfile(id);
    } catch (e) {
      if (prev) setProfiles((rows) => rows.map((r) => (r.id === id ? prev : r)));
      onError?.((e as Error).message);
    } finally {
      setSavingId(null);
    }
  };

  const saveField = async (
    id: string,
    field: "priority" | "comments" | "followers" | "location" | "last_post_date",
    value: string | number,
  ) => {
    const prev = profiles.find((r) => r.id === id);
    setProfiles((rows) => rows.map((r) => (r.id === id ? { ...r, [field]: value } : r)));
    try {
      await profilesApi.patchProfile(id, { [field]: value } as Record<string, unknown>);
    } catch (e) {
      if (prev) setProfiles((rows) => rows.map((r) => (r.id === id ? prev : r)));
      onError?.((e as Error).message);
    }
  };

  const handleCopyUrls = async () => {
    const approved = displayed.filter((r) => r.status === "approved");
    if (!approved.length) {
      onError?.("No approved profiles on this page to copy.");
      return;
    }
    try {
      await navigator.clipboard.writeText(approved.map((r) => r.url).join("\n"));
      setCopyUrlState("copied");
    } catch {
      setCopyUrlState("failed");
    } finally {
      setTimeout(() => setCopyUrlState("idle"), 2000);
    }
  };

  // Fetches everything matching the current filters (not just this page) for
  // export -- this backend has no export endpoint, so the conversion happens
  // entirely client-side.
  const handleExport = async (fmt: "csv" | "json") => {
    if (!clientId) return;
    setExporting(true);
    try {
      const res = await profilesApi.profiles({
        client_id: clientId,
        platform: platform || undefined,
        status: !isAnalysisView && status ? status : undefined,
        phase,
        limit: EXPORT_LIMIT,
        offset: 0,
      });
      const filtered = filterResults(res.items, filters, extra, platform);
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      if (fmt === "csv") {
        download(`${clientId}-${phase}-${stamp}.csv`, toCsv(filtered), "text/csv");
      } else {
        download(
          `${clientId}-${phase}-${stamp}.json`,
          JSON.stringify(filtered, null, 2),
          "application/json",
        );
      }
    } catch (e) {
      onError?.((e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div style={{ animation: "fadeUp 0.4s ease" }}>
      {(discoveryRunning || analysisRunning) && (
        <div className="scanning-banner">
          <span
            style={{
              width: "12px", height: "12px", borderRadius: "50%",
              background: "var(--success)", boxShadow: "0 0 12px var(--success)",
              animation: "blink 1.2s infinite",
            }}
          />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: "14px", fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--text-main)" }}>
              Scanning platforms for active impersonation signals…
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {[discoveryRunning && "Discovery running", analysisRunning && "Analysis running"]
                .filter(Boolean)
                .join(" · ")}{" "}
              · {total} matching profile{total === 1 ? "" : "s"}
            </div>
          </div>
          <div
            style={{
              width: "22px", height: "22px", border: "2px solid rgba(136, 56, 221, 0.25)",
              borderTopColor: "var(--cyan)", borderRadius: "50%", animation: "spin 0.8s linear infinite",
            }}
          />
        </div>
      )}

      <div className="content-shell">
        <div className="content-shell-inner">
          {/* Phase tabs */}
          <div className="platform-rail-grid" style={{ gridTemplateColumns: "repeat(2, 1fr)", marginBottom: "16px" }}>
            {(["discovery", "analysis"] as const).map((ph) => (
              <div
                key={ph}
                className={`platform-rail-item ${phase === ph ? "active" : ""}`}
                onClick={() => setPhase(ph)}
              >
                <div className="rail-card-head">
                  <span style={{ fontSize: "16px" }}>{ph === "discovery" ? "🔍" : "📊"}</span>
                  <span style={{ fontSize: "12px", fontWeight: 500, color: "var(--text-primary)" }}>
                    {ph === "discovery" ? "Discovery" : "Analysis"}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {/* Platform filter rail -- view-only. Discovery/analysis on this
              backend always run across every ready platform at once, so
              there is nothing per-platform to launch from here anymore. */}
          <div className="platform-rail-grid">
            <div
              className={`platform-rail-item ${platform === "" ? "active" : ""}`}
              onClick={() => setPlatform("")}
            >
              <div className="rail-card-head">
                <span style={{ fontSize: "16px" }}>🌐</span>
                <span style={{ fontSize: "12px", fontWeight: 500 }}>All Platforms</span>
              </div>
              <div className="rail-card-foot">
                <span className="rail-pill" style={{ color: "var(--text-main)", fontWeight: 700 }}>
                  {Object.values(counts.platforms).reduce((a, b) => a + b, 0)} results
                </span>
              </div>
            </div>
            {platforms.map((p) => {
              const count = counts.platforms[p.platform] || 0;
              return (
                <div
                  key={p.platform}
                  className={`platform-rail-item ${platform === p.platform ? "active" : ""}`}
                  onClick={() => setPlatform(p.platform)}
                >
                  <div className="rail-card-head">
                    <PlatformIcon platform={p.platform} size={18} />
                    <span style={{ fontSize: "12px", fontWeight: 500 }}>{p.name}</span>
                  </div>
                  <div className="rail-card-foot" style={{ display: "flex", justifyContent: "space-between", width: "100%" }}>
                    <span
                      className="rail-pill"
                      style={{ color: p.session_state === "ready" ? "var(--success)" : "var(--text-dim)" }}
                    >
                      {p.session_state}
                    </span>
                    <span className="rail-pill" style={{ color: count > 0 ? "var(--text-main)" : "var(--text-dim)", fontWeight: count > 0 ? 700 : 400 }}>
                      {count} {count === 1 ? "result" : "results"}
                    </span>
                  </div>
                  {(discoveryRunning || phase === "discovery") && discoveryProgress[p.platform] && (
                    <PlatformProgressRow label="🔍 Discovery" progress={discoveryProgress[p.platform]} />
                  )}
                  {(analysisRunning || phase === "analysis") && analysisProgress[p.platform] && (
                    <PlatformProgressRow label="📊 Analysis" progress={analysisProgress[p.platform]} />
                  )}
                </div>
              );
            })}
          </div>

          {discoveryRunning && analysisRunning && (
            <div
              className="dashboard-card-box"
              style={{
                marginTop: "16px",
                borderLeft: "4px solid var(--warn-yellow)",
                background: "linear-gradient(90deg, rgba(0,229,255,0.05), rgba(136,56,221,0.05))",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "10px" }}>
                <span style={{ fontSize: "16px" }}>⚡</span>
                <span style={{ fontWeight: 700, fontSize: "13px", color: "var(--text-main)" }}>
                  Both Jobs Running — Live Split by Platform
                </span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                {platforms.map((p) => {
                  const d = discoveryProgress[p.platform];
                  const a = analysisProgress[p.platform];
                  if (!d && !a) return null;
                  return (
                    <div
                      key={p.platform}
                      style={{
                        display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap",
                        background: "var(--bg-surface)", borderRadius: "10px", padding: "7px 12px",
                        border: "1px solid var(--border-subtle)",
                      }}
                    >
                      <PlatformIcon platform={p.platform} size={16} />
                      <span style={{ fontSize: "12px", fontWeight: 700, textTransform: "capitalize", minWidth: "70px" }}>
                        {p.name}
                      </span>
                      <span style={{ flex: 1 }} />
                      <span style={{ display: "flex", alignItems: "center", gap: "5px", fontSize: "11px", color: d ? PLATFORM_STATUS_LOOK[d.status].color : "var(--text-dim)" }}>
                        🔍 {d ? `${PLATFORM_STATUS_LOOK[d.status].icon} ${d.processed}/${d.total || "?"}${d.status === "running" && d.eta_seconds !== null ? ` · ${formatEta(d.eta_seconds)}` : ""}` : "idle"}
                      </span>
                      <span style={{ display: "flex", alignItems: "center", gap: "5px", fontSize: "11px", color: a ? PLATFORM_STATUS_LOOK[a.status].color : "var(--text-dim)" }}>
                        📊 {a ? `${PLATFORM_STATUS_LOOK[a.status].icon} ${a.processed}/${a.total || "?"}${a.status === "running" && a.eta_seconds !== null ? ` · ${formatEta(a.eta_seconds)}` : ""}` : "idle"}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {(discoveryRunning || (phase === "discovery" && Object.keys(discoveryProgress).length > 0)) && (
            <div className="dashboard-card-box" style={{ marginTop: "16px", borderLeft: "4px solid var(--cyan)", background: "rgba(0, 229, 255, 0.04)" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px", flexWrap: "wrap", gap: "8px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span style={{ fontSize: "18px" }}>🔍</span>
                  <span style={{ fontWeight: 700, color: "var(--text-main)", fontSize: "14px" }}>
                    {discoveryRunning ? "Live Discovery Sweep Progress" : "Recent Discovery Status"}
                  </span>
                  {discoveryRunning ? (
                    <span className="rail-pill" style={{ background: "var(--cyan)", color: "#000", fontWeight: 700, animation: "pulse 1.5s infinite" }}>
                      RUNNING
                    </span>
                  ) : (
                    <span className="rail-pill" style={{ background: "rgba(0,193,77,0.2)", color: "var(--success)", fontWeight: 700 }}>
                      COMPLETED
                    </span>
                  )}
                </div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 700, color: "var(--cyan)" }}>
                  {Object.values(discoveryProgress).reduce((acc, p) => acc + (p.processed || 0), 0)} / {Object.values(discoveryProgress).reduce((acc, p) => acc + (p.total || 0), 0) || "?"} Sweeps Completed
                </div>
              </div>
              <div style={{ height: "8px", background: "var(--bg-inner)", borderRadius: "4px", overflow: "hidden" }}>
                <div
                  style={{
                    height: "100%",
                    width: `${Math.min(100, Math.round((Object.values(discoveryProgress).reduce((acc, p) => acc + (p.processed || 0), 0) / (Object.values(discoveryProgress).reduce((acc, p) => acc + (p.total || 0), 0) || 1)) * 100))}%`,
                    background: "linear-gradient(90deg, var(--cyan), var(--purple))",
                    transition: "width 0.4s ease",
                  }}
                />
              </div>
              <div style={{ display: "flex", gap: "10px", marginTop: "12px", flexWrap: "wrap" }}>
                {Object.entries(discoveryProgress).map(([plat, prog]) => (
                  <div key={plat} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", background: "var(--bg-surface)", padding: "5px 12px", borderRadius: "16px", border: "1px solid var(--border-color)" }}>
                    <PlatformIcon platform={plat} size={15} />
                    <span style={{ fontWeight: 600, textTransform: "capitalize", color: "var(--text-main)" }}>{plat}:</span>
                    <span style={{ color: PLATFORM_STATUS_LOOK[prog.status]?.color || "var(--text-main)", fontWeight: 700 }}>
                      {PLATFORM_STATUS_LOOK[prog.status]?.icon} {prog.processed}/{prog.total}
                    </span>
                    {prog.eta_seconds !== null && prog.status === "running" && (
                      <span style={{ fontSize: "11px", color: "var(--text-dim)", marginLeft: "4px" }}>
                        ({formatEta(prog.eta_seconds)})
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {(analysisRunning || (phase === "analysis" && Object.keys(analysisProgress).length > 0)) && (
            <div className="dashboard-card-box" style={{ marginTop: "16px", borderLeft: "4px solid var(--purple)", background: "rgba(136, 56, 221, 0.05)" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px", flexWrap: "wrap", gap: "8px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span style={{ fontSize: "18px" }}>📊</span>
                  <span style={{ fontWeight: 700, color: "var(--text-main)", fontSize: "14px" }}>
                    {analysisRunning ? "Live Analysis Progress" : "Recent Analysis Status"}
                  </span>
                  {analysisRunning ? (
                    <span className="rail-pill" style={{ background: "var(--purple)", color: "#fff", fontWeight: 700, animation: "pulse 1.5s infinite" }}>
                      RUNNING
                    </span>
                  ) : (
                    <span className="rail-pill" style={{ background: "rgba(0,193,77,0.2)", color: "var(--success)", fontWeight: 700 }}>
                      COMPLETED
                    </span>
                  )}
                </div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 700, color: "var(--purple)" }}>
                  {Object.values(analysisProgress).reduce((acc, p) => acc + (p.processed || 0), 0)} / {Object.values(analysisProgress).reduce((acc, p) => acc + (p.total || 0), 0) || "?"} Profiles Analysed
                  {Object.values(analysisProgress).reduce((acc, p) => acc + (p.total || 0), 0) > 0 ? ` (${Math.round((Object.values(analysisProgress).reduce((acc, p) => acc + (p.processed || 0), 0) / Object.values(analysisProgress).reduce((acc, p) => acc + (p.total || 0), 0)) * 100)}%)` : ""}
                </div>
              </div>
              <div style={{ height: "8px", background: "var(--bg-inner)", borderRadius: "4px", overflow: "hidden" }}>
                <div
                  style={{
                    height: "100%",
                    width: `${Math.min(100, Math.round((Object.values(analysisProgress).reduce((acc, p) => acc + (p.processed || 0), 0) / (Object.values(analysisProgress).reduce((acc, p) => acc + (p.total || 0), 0) || 1)) * 100))}%`,
                    background: "linear-gradient(90deg, var(--purple), var(--cyan), var(--success))",
                    transition: "width 0.4s ease",
                  }}
                />
              </div>
              <div style={{ display: "flex", gap: "10px", marginTop: "12px", flexWrap: "wrap" }}>
                {Object.entries(analysisProgress).map(([plat, prog]) => (
                  <div key={plat} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", background: "var(--bg-surface)", padding: "5px 12px", borderRadius: "16px", border: "1px solid var(--border-color)" }}>
                    <PlatformIcon platform={plat} size={15} />
                    <span style={{ fontWeight: 600, textTransform: "capitalize", color: "var(--text-main)" }}>{plat}:</span>
                    <span style={{ color: PLATFORM_STATUS_LOOK[prog.status]?.color || "var(--text-main)", fontWeight: 700 }}>
                      {PLATFORM_STATUS_LOOK[prog.status]?.icon} {prog.processed}/{prog.total}
                    </span>
                    {prog.eta_seconds !== null && prog.status === "running" && (
                      <span style={{ fontSize: "11px", color: "var(--text-dim)", marginLeft: "4px" }}>
                        ({formatEta(prog.eta_seconds)})
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {discoveryRunning && discoveryLog.length > 0 && <LiveFeed title="Discovery Feed" log={discoveryLog} />}
          {analysisRunning && analysisLog.length > 0 && <LiveFeed title="Analysis Feed" log={analysisLog} />}

          {!isAnalysisView && (
            <div className="status-summary-row" style={{ marginTop: "16px", display: "flex", gap: "8px", flexWrap: "wrap" }}>
              {(["pending", "approved", "rejected"] as const).map((s) => {
                const count = counts.statuses[s] ?? 0;
                const look = {
                  pending: { label: "⏳ Pending", color: "var(--purple)", text: "var(--text-main)" },
                  approved: { label: "✅ Validated", color: "var(--success)", text: "var(--success)" },
                  rejected: { label: "✕ Rejected", color: "var(--danger)", text: "var(--danger)" },
                }[s];
                return (
                  <button
                    key={s}
                    className={`status-chip ${status === s ? "on" : ""}`}
                    onClick={() => setStatus(status === s ? "" : s)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      padding: "6px 14px",
                      borderRadius: "20px",
                      border: `1px solid ${status === s ? look.color : "var(--border-color)"}`,
                      background: status === s ? "var(--bg-surface)" : "transparent",
                      cursor: "pointer",
                      fontSize: "12px",
                      fontWeight: 600,
                      color: status === s ? look.text : "var(--text-muted)",
                      transition: "all 0.2s ease",
                    }}
                  >
                    <span>{look.label}</span>
                    <span
                      style={{
                        background: status === s ? look.color : "var(--bg-inner)",
                        color: status === s ? "#fff" : "var(--text-dim)",
                        padding: "2px 8px",
                        borderRadius: "12px",
                        fontSize: "11px",
                        fontWeight: 700,
                      }}
                    >
                      {count}
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          {/* Filter toolbar */}
          <div className="filter-toolbar" style={{ marginTop: "12px" }}>
            {isAnalysisView ? (
              <input
                value={keywordFilter}
                onChange={(e) => setKeywordFilter(e.target.value)}
                placeholder="Filter by keyword…"
                className="input-filter"
                title="No server-side keyword index for analysis rows -- filters whatever's on the current page"
              />
            ) : (
              <select
                value={keywordFilter}
                onChange={(e) => setKeywordFilter(e.target.value)}
                className="select-filter"
                title="Only show profiles found by this exact keyword"
              >
                <option value="">All Keywords</option>
                {Object.entries(counts.keywords)
                  .sort((a, b) => b[1] - a[1])
                  .map(([kw, n]) => (
                    <option key={kw} value={kw}>
                      🔑 {kw} ({n})
                    </option>
                  ))}
              </select>
            )}
            {!isAnalysisView && (
              <select
                value={matchLevel}
                onChange={(e) => setMatchLevel(e.target.value as "" | "high" | "low")}
                className="select-filter"
                title="How closely the scraped name matches the keyword that found it"
              >
                <option value="">All Match Levels</option>
                <option value="high">🎯 High Match</option>
                <option value="low">🎯 Low Match</option>
              </select>
            )}
            {!isAnalysisView && isFacebook && (
              <select
                value={entityType}
                onChange={(e) => setEntityType(e.target.value as "" | "profile" | "page")}
                className="select-filter"
                title="Facebook only distinguishes people profiles from Pages -- filter to just one"
              >
                <option value="">People + Pages</option>
                <option value="profile">👤 People Only</option>
                <option value="page">📄 Pages Only</option>
              </select>
            )}
            {isAnalysisView && (
              <>
                <select value={sortOrder} onChange={(e) => setSortOrder(e.target.value as "recent" | "past")} className="select-filter">
                  <option value="recent">Sort: Highest Risk / Score</option>
                  <option value="past">Sort: Lowest Risk / Score</option>
                </select>
                <select value={priority} onChange={(e) => setPriority(e.target.value)} className="select-filter">
                  <option value="">All Priorities</option>
                  <option value="High">HIGH Priority</option>
                  <option value="Medium">MEDIUM Priority</option>
                  <option value="Low">LOW Priority</option>
                </select>
              </>
            )}
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="🔎 Search name / handle…"
              className="input-filter"
              style={{ flex: 1, minWidth: "160px" }}
            />
            <div style={{ display: "flex", gap: "6px" }}>
              <button
                onClick={() => setViewMode("grid")}
                title="Card view"
                style={{
                  background: viewMode === "grid" ? "rgba(136, 56, 221,0.12)" : "var(--bg-surface)",
                  border: `1px solid ${viewMode === "grid" ? "var(--cyan)" : "var(--border-color)"}`,
                  color: viewMode === "grid" ? "var(--text-main)" : "var(--text-muted)",
                  borderRadius: "8px",
                  padding: "7px 10px",
                  cursor: "pointer",
                }}
              >
                📱 Cards
              </button>
              <button
                onClick={() => setViewMode("table")}
                title="Table view"
                style={{
                  background: viewMode === "table" ? "rgba(136, 56, 221,0.12)" : "var(--bg-surface)",
                  border: `1px solid ${viewMode === "table" ? "var(--cyan)" : "var(--border-color)"}`,
                  color: viewMode === "table" ? "var(--text-main)" : "var(--text-muted)",
                  borderRadius: "8px",
                  padding: "7px 10px",
                  cursor: "pointer",
                }}
              >
                📋 Table
              </button>
            </div>
            <button className="btn-cyber-primary" style={{ padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto" }} onClick={() => handleExport("csv")} disabled={exporting || !clientId}>
              {exporting ? "…" : "CSV"}
            </button>
            <button className="btn-cyber-primary" style={{ padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto" }} onClick={() => handleExport("json")} disabled={exporting || !clientId}>
              {exporting ? "…" : "JSON"}
            </button>
            <button
              className="btn-cyber-primary"
              style={{
                padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto",
                background: copyUrlState === "copied" ? "var(--success)" : copyUrlState === "failed" ? "var(--danger)" : "rgba(0, 193, 77, 0.15)",
                color: "var(--success)", border: "1px solid var(--success)",
              }}
              onClick={handleCopyUrls}
              title="Copy this page's approved profile URLs to clipboard"
            >
              {copyUrlState === "copied" ? "✓ Copied" : copyUrlState === "failed" ? "✕ Failed" : "📋 Copy URLs"}
            </button>
          </div>

          {loading && <div style={{ padding: "24px", textAlign: "center", color: "var(--text-dim)" }}>Loading…</div>}

          {!loading && !clientId && (
            <div style={{ padding: "24px", textAlign: "center", color: "var(--text-dim)" }}>
              Set a client on Live Discovery to see results.
            </div>
          )}

          {!loading && clientId && !displayed.length && (
            <div style={{ padding: "24px", textAlign: "center", color: "var(--text-dim)" }}>
              No profiles match the current filters.
            </div>
          )}

          {!loading && displayed.length > 0 && viewMode === "grid" && (
            <div className="profile-grid-container" style={{ marginTop: "12px" }}>
              {displayed.map((r) => (
                <ProfileCard key={r.id} r={r} isAnalysisView={isAnalysisView} savingId={savingId} onDecide={decide} onPublish={publish} onValidate={validate} />
              ))}
            </div>
          )}

          {!loading && displayed.length > 0 && viewMode === "table" && (
            <div style={{ overflowX: "auto", marginTop: "12px" }}>
              <table className="core_table">
                <thead>
                  <tr>
                    <th></th>
                    <th>Name</th>
                    <th>Platform</th>
                    {isAnalysisView && <th>Username</th>}
                    {isAnalysisView && <th>Followers</th>}
                    {isAnalysisView && <th>Location</th>}
                    {isAnalysisView && <th>Last Post</th>}
                    {isAnalysisView && <th>Risk</th>}
                    {isAnalysisView && <th>Priority</th>}
                    {isAnalysisView && <th>Comments</th>}
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((r) => {
                    const isHeld = isAnalysisView && r.published === false;
                    return (
                    <tr key={r.id}>
                      <td>
                        <ProfileAvatar r={r} size={28} />
                      </td>
                      <td>
                        <a href={r.url} target="_blank" rel="noreferrer" style={{ color: "var(--text-main)" }}>
                          {r.profile_name || r.username || r.url}
                        </a>
                        {r.verified && <span className="verified-check" title="Verified account on this platform"> ✓</span>}
                        {r.has_logo && <span title="Uses a logo/brand photo"> 🏷️</span>}
                      </td>
                      <td><PlatformIcon platform={r.platform} size={16} /></td>
                      {isAnalysisView && <td>{r.username || "—"}</td>}
                      {isAnalysisView && (
                        <td>
                          <input
                            type="number"
                            defaultValue={r.followers ?? ""}
                            placeholder={emptyLabel(r, r.platform, "followers")}
                            onBlur={(e) => {
                              const v = Number(e.target.value);
                              if (!Number.isNaN(v) && v !== r.followers) saveField(r.id, "followers", v);
                            }}
                            style={{ width: "90px" }}
                            className="input-filter"
                          />
                        </td>
                      )}
                      {isAnalysisView && (
                        <td>
                          <input
                            defaultValue={r.location ?? ""}
                            placeholder={emptyLabel(r, r.platform, "location")}
                            onBlur={(e) => {
                              if (e.target.value !== (r.location ?? "")) saveField(r.id, "location", e.target.value);
                            }}
                            style={{ width: "100px" }}
                            className="input-filter"
                          />
                        </td>
                      )}
                      {isAnalysisView && (
                        <td>
                          <input
                            defaultValue={r.last_post_date ?? ""}
                            placeholder={emptyLabel(r, r.platform, "last_post_date")}
                            onBlur={(e) => {
                              if (e.target.value !== (r.last_post_date ?? "")) saveField(r.id, "last_post_date", e.target.value);
                            }}
                            style={{ width: "100px" }}
                            className="input-filter"
                          />
                        </td>
                      )}
                      {isAnalysisView && <td>{r.risk_score ?? "—"}</td>}
                      {isAnalysisView && (
                        <td>
                          <select
                            value={r.priority ?? ""}
                            onChange={(e) => saveField(r.id, "priority", e.target.value)}
                            className="select-filter"
                          >
                            <option value="">—</option>
                            <option value="High">High</option>
                            <option value="Medium">Medium</option>
                            <option value="Low">Low</option>
                          </select>
                        </td>
                      )}
                      {isAnalysisView && (
                        <td>
                          <input
                            defaultValue={r.comments ?? ""}
                            onBlur={(e) => {
                              if (e.target.value !== (r.comments ?? "")) saveField(r.id, "comments", e.target.value);
                            }}
                            style={{ width: "140px" }}
                            className="input-filter"
                          />
                        </td>
                      )}
                      <td>
                        <span className="status-chip on" style={{ cursor: "default" }}>
                          {r.status}
                        </span>
                        {isHeld && (
                          <div
                            style={{ fontSize: "10px", color: "var(--purple)", marginTop: "3px", whiteSpace: "nowrap" }}
                            title="Not yet visible outside this tool -- gives you a window to revert a false positive before it's published"
                          >
                            ⏳ {holdRemainingLabel(r.publish_hold_until)}
                          </div>
                        )}
                      </td>
                      <td>
                        {r.status !== "approved" && (
                          <button
                            disabled={savingId === r.id}
                            onClick={() => decide(r.id, "approved")}
                            style={{ marginRight: "4px", background: "rgba(0,193,77,0.12)", color: "var(--success)", border: "1px solid rgba(0,193,77,0.3)", borderRadius: "6px", padding: "4px 8px", cursor: "pointer" }}
                          >
                            ✓ Approve
                          </button>
                        )}
                        {r.status !== "rejected" && (
                          <button
                            disabled={savingId === r.id}
                            onClick={() => decide(r.id, "rejected")}
                            style={{ marginRight: "4px", background: "rgba(233,80,83,0.1)", color: "var(--danger)", border: "1px solid rgba(233,80,83,0.25)", borderRadius: "6px", padding: "4px 8px", cursor: "pointer" }}
                          >
                            ✕ Reject
                          </button>
                        )}
                        {isHeld && (
                          <button
                            disabled={savingId === r.id}
                            onClick={() => publish(r.id)}
                            title="Skip the rest of the hold and publish this result now"
                            style={{ background: "rgba(136,56,221,0.12)", color: "var(--purple)", border: "1px solid rgba(136,56,221,0.3)", borderRadius: "6px", padding: "4px 8px", cursor: "pointer" }}
                          >
                            📢 Publish
                          </button>
                        )}
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {!loading && total > PAGE_SIZE && (
            <div style={{ display: "flex", justifyContent: "center", gap: "10px", alignItems: "center", marginTop: "16px" }}>
              <button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                className="btn-cyber-primary"
                style={{ width: "auto", padding: "6px 12px", marginTop: 0 }}
              >
                ← Prev
              </button>
              <span style={{ fontSize: "12px", color: "var(--text-dim)" }}>
                Page {currentPage} of {pageCount} · {total} total
              </span>
              <button
                disabled={currentPage >= pageCount}
                onClick={() => setOffset(offset + PAGE_SIZE)}
                className="btn-cyber-primary"
                style={{ width: "auto", padding: "6px 12px", marginTop: 0 }}
              >
                Next →
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
