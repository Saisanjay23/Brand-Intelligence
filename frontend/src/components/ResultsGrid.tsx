import { useCallback, useEffect, useMemo, useState } from "react";
import { profilesApi } from "../api/profilesApi";
import type { JobEvent, PlatformHealth, Profile, Status } from "../api/types";
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
  analysisRunning: boolean;
  analysisLog: JobEvent[];
  onError?: (msg: string) => void;
}

const PAGE_SIZE = 25;
const EXPORT_LIMIT = 5000;

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
  if (!r.profile_image_url) {
    return (
      <span
        className="profile-avatar-circle"
        style={size ? { width: size, height: size, fontSize: size * 0.45, borderRadius: "50%" } : undefined}
      >
        {(r.profile_name || r.username || "?").charAt(0).toUpperCase()}
      </span>
    );
  }
  return (
    <img
      src={r.profile_image_url}
      alt=""
      referrerPolicy="no-referrer"
      loading="lazy"
      style={size ? { width: size, height: size, borderRadius: "50%", objectFit: "cover" } : { width: "100%", height: "100%", objectFit: "cover" }}
      onError={(e) => ((e.target as HTMLImageElement).style.visibility = "hidden")}
    />
  );
}

interface CardProps {
  r: Profile;
  isAnalysisView: boolean;
  savingId: string | null;
  onDecide: (id: string, next: Status) => void;
}

function ProfileCard({ r, isAnalysisView, savingId, onDecide }: CardProps) {
  const name = r.profile_name || r.username || r.url;
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
          {r.has_logo && (
            <span className="verified-check" title="Uses a logo/brand photo">
              ✓
            </span>
          )}
        </div>
        {isAnalysisView && r.username && <div className="profile-handle">@{r.username}</div>}

        {isAnalysisView && (
          <div className="card-detail-row">
            <span>👥 {r.followers ?? emptyLabel(r, r.platform, "followers")}</span>
            <span>📍 {r.location || emptyLabel(r, r.platform, "location")}</span>
            <span>🕐 {r.last_post_date || emptyLabel(r, r.platform, "last_post_date")}</span>
          </div>
        )}

        <div className="card-meta-row">
          <span style={{ fontSize: "11px", color: "var(--text-dim)" }}>
            {isAnalysisView ? `Risk ${r.risk_score ?? "—"}` : r.comments || ""}
          </span>
        </div>

        <div className="card-actions-row">
          {r.status !== "approved" && (
            <button className="btn-accept" disabled={savingId === r.id} onClick={() => onDecide(r.id, "approved")}>
              ✓ Approve
            </button>
          )}
          {r.status !== "rejected" && (
            <button className="btn-reject" disabled={savingId === r.id} onClick={() => onDecide(r.id, "rejected")}>
              ✕ Reject
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
  analysisRunning,
  analysisLog,
  onError,
}: Props) {
  const [platform, setPlatform] = useState("");
  const [phase, setPhase] = useState<"discovery" | "analysis">("discovery");
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [sortOrder, setSortOrder] = useState<"recent" | "past">("recent");
  const [keywordFilter, setKeywordFilter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [viewMode, setViewMode] = useState<"grid" | "table">("table");

  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [copyUrlState, setCopyUrlState] = useState<"idle" | "copied" | "failed">("idle");

  const isAnalysisView = phase === "analysis";

  const load = useCallback(
    async (showLoading = true) => {
      if (!clientId) {
        setProfiles([]);
        setTotal(0);
        return;
      }
      if (showLoading) setLoading(true);
      try {
        const res = await profilesApi.profiles({
          client_id: clientId,
          platform: platform || undefined,
          status: !isAnalysisView && status ? status : undefined,
          phase,
          limit: PAGE_SIZE,
          offset,
        });
        setProfiles(res.items);
        setTotal(res.total);
      } catch (e) {
        onError?.((e as Error).message);
      } finally {
        if (showLoading) setLoading(false);
      }
    },
    [clientId, platform, status, phase, offset, isAnalysisView, onError],
  );

  useEffect(() => {
    setOffset(0);
  }, [clientId, platform, status, phase]);

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

  const filters: ResultFilters = { status, priority, phase };
  const extra: ExtraFilters = { keywordFilter, searchQuery };
  const displayed = useMemo(
    () => sortResults(filterResults(profiles, filters, extra, platform), sortOrder, phase, keywordFilter),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [profiles, status, priority, phase, keywordFilter, searchQuery, sortOrder, platform],
  );

  const decide = async (id: string, next: Status) => {
    const prev = profiles.find((r) => r.id === id);
    setProfiles((rows) => rows.map((r) => (r.id === id ? { ...r, status: next } : r)));
    setSavingId(id);
    try {
      await profilesApi.patchProfile(id, { status: next });
      // approving auto-queues analysis server-side -- nothing else to do here
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
            </div>
            {platforms.map((p) => (
              <div
                key={p.platform}
                className={`platform-rail-item ${platform === p.platform ? "active" : ""}`}
                onClick={() => setPlatform(p.platform)}
              >
                <div className="rail-card-head">
                  <PlatformIcon platform={p.platform} size={18} />
                  <span style={{ fontSize: "12px", fontWeight: 500 }}>{p.name}</span>
                </div>
                <div className="rail-card-foot">
                  <span
                    className="rail-pill"
                    style={{ color: p.session_state === "ready" ? "var(--success)" : "var(--text-dim)" }}
                  >
                    {p.session_state}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {discoveryRunning && discoveryLog.length > 0 && <LiveFeed title="Discovery Feed" log={discoveryLog} />}
          {analysisRunning && analysisLog.length > 0 && <LiveFeed title="Analysis Feed" log={analysisLog} />}

          {!isAnalysisView && (
            <div className="status-summary-row" style={{ marginTop: "16px" }}>
              {(["pending", "approved", "rejected"] as const).map((s) => (
                <span
                  key={s}
                  className={`status-chip ${status === s ? "on" : ""}`}
                  onClick={() => setStatus(status === s ? "" : s)}
                >
                  {s}
                </span>
              ))}
            </div>
          )}

          {/* Filter toolbar */}
          <div className="filter-toolbar" style={{ marginTop: "12px" }}>
            <input
              value={keywordFilter}
              onChange={(e) => setKeywordFilter(e.target.value)}
              placeholder="Filter by keyword…"
              className="input-filter"
              title="No server-side keyword index on this backend -- filters whatever's on the current page"
            />
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
                  color: viewMode === "grid" ? "var(--cyan)" : "var(--text-muted)",
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
                  color: viewMode === "table" ? "var(--cyan)" : "var(--text-muted)",
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
                <ProfileCard key={r.id} r={r} isAnalysisView={isAnalysisView} savingId={savingId} onDecide={decide} />
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
                  {displayed.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <ProfileAvatar r={r} size={28} />
                      </td>
                      <td>
                        <a href={r.url} target="_blank" rel="noreferrer" style={{ color: "var(--text-main)" }}>
                          {r.profile_name || r.username || r.url}
                        </a>
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
                            style={{ background: "rgba(233,80,83,0.1)", color: "var(--danger)", border: "1px solid rgba(233,80,83,0.25)", borderRadius: "6px", padding: "4px 8px", cursor: "pointer" }}
                          >
                            ✕ Reject
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
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
