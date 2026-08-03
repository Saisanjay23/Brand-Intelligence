import { useEffect, useState } from "react";
import { incidentsApi } from "../api/incidentsApi";
import { profilesApi } from "../api/profilesApi";
import type { Incident, JobEvent, PlatformHealth, SessionInfo } from "../api/types";

interface Stats {
  total: number;
  analysed: number;
  approved: number;
  rejected: number;
  pending: number;
}

const EMPTY_STATS: Stats = { total: 0, analysed: 0, approved: 0, rejected: 0, pending: 0 };

interface Props {
  clientId: string;
  activeJobsCount: number;
  platforms: PlatformHealth[];
  sessions: SessionInfo[];
  logs: JobEvent[];
}

export function DashboardView({ clientId, activeJobsCount, platforms, sessions, logs }: Props) {
  const [stats, setStats] = useState<Stats>(EMPTY_STATS);
  const [platformCounts, setPlatformCounts] = useState<Record<string, number>>({});
  const [incidents, setIncidents] = useState<Incident[]>([]);

  // This backend has no /profiles/stats aggregate -- each count is a
  // separate limit=1 query that only reads back `total`, cheap enough to
  // fire in parallel for a dashboard refresh.
  useEffect(() => {
    if (!clientId) {
      setStats(EMPTY_STATS);
      setPlatformCounts({});
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const [total, analysed, approved, rejected, pending] = await Promise.all([
          profilesApi.profiles({ client_id: clientId, limit: 1 }),
          profilesApi.profiles({ client_id: clientId, phase: "analysis", limit: 1 }),
          profilesApi.profiles({ client_id: clientId, status: "approved", limit: 1 }),
          profilesApi.profiles({ client_id: clientId, status: "rejected", limit: 1 }),
          profilesApi.profiles({ client_id: clientId, status: "pending", limit: 1 }),
        ]);
        if (cancelled) return;
        setStats({
          total: total.total,
          analysed: analysed.total,
          approved: approved.total,
          rejected: rejected.total,
          pending: pending.total,
        });

        const perPlatform = await Promise.all(
          platforms.map((p) =>
            profilesApi.profiles({ client_id: clientId, platform: p.platform, limit: 1 }).then((r) => [p.platform, r.total] as const),
          ),
        );
        if (!cancelled) setPlatformCounts(Object.fromEntries(perPlatform));
      } catch {
        // dashboard is a read-only summary -- a failed refresh just leaves
        // the previous numbers on screen rather than surfacing an error banner
      }
    };
    load();
    const interval = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [clientId, platforms]);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      incidentsApi
        .incidents(30)
        .then((res) => {
          if (!cancelled) setIncidents(res.items);
        })
        .catch(() => {});
    load();
    const interval = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const getPlatformColor = (id: string) => {
    switch (id) {
      case "facebook":
        return "#1877F2";
      case "instagram":
        return "#E1306C";
      case "twitter":
        return "#00d4ff";
      case "youtube":
        return "#FF0000";
      case "telegram":
        return "#26A5E4";
      default:
        return "#8838DD";
    }
  };

  return (
    <div style={{ animation: "fadeUp 0.4s ease" }}>
      <div className="dashboard-grid">
        <div className="stat-tile" style={{ background: "linear-gradient(135deg, #8838DD, #9A50E9)", boxShadow: "0 8px 30px rgba(136, 56, 221, 0.25)" }}>
          <div className="stat-tile-num">{stats.analysed}</div>
          <div className="stat-tile-label">Analysed Profiles</div>
        </div>

        <div className="stat-tile" style={{ background: "linear-gradient(135deg, #12B76A, #00C14D)", boxShadow: "0 8px 30px rgba(0, 193, 77, 0.25)" }}>
          <div className="stat-tile-num">{stats.approved}</div>
          <div className="stat-tile-label">Validated</div>
        </div>

        <div className="stat-tile" style={{ background: "linear-gradient(135deg, #FF8000, #FDB71B)", boxShadow: "0 8px 30px rgba(255, 128, 0, 0.25)" }}>
          <div className="stat-tile-num">{activeJobsCount}</div>
          <div className="stat-tile-label">Active Jobs</div>
        </div>

        <div className="stat-tile" style={{ background: "linear-gradient(135deg, #7727CD, #8838DD)", boxShadow: "0 8px 30px rgba(119, 39, 205, 0.25)" }}>
          <div className="stat-tile-num">{stats.total}</div>
          <div className="stat-tile-label">Profiles Found</div>
        </div>
      </div>

      <div className="dashboard-split-2">
        <div className="dashboard-card-box">
          <div style={{ fontSize: "13px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "1px", marginBottom: "16px", color: "var(--text-muted)" }}>
            Per-Platform Distribution ({clientId || "no client set"})
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {platforms.map((p) => {
              const count = platformCounts[p.platform] ?? 0;
              const maxCount = Math.max(...Object.values(platformCounts), 1);
              const pct = Math.round((count / maxCount) * 100);
              return (
                <div key={p.platform} className="platform-bar-row">
                  <span style={{ fontSize: "12px", fontWeight: 600, width: "84px", color: "var(--text-main)" }}>{p.name}</span>
                  <div className="platform-bar-bg">
                    <div className="platform-bar-fill" style={{ width: `${pct || 4}%`, background: getPlatformColor(p.platform) }} />
                  </div>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--text-muted)", width: "36px", textAlign: "right" }}>
                    {count}
                  </span>
                </div>
              );
            })}
          </div>

          <div style={{ display: "flex", gap: "12px", marginTop: "20px", paddingTop: "16px", borderTop: "1px solid var(--border-subtle)" }}>
            <div style={{ flex: 1, textAlign: "center", background: "rgba(0, 193, 77, 0.06)", border: "1px solid rgba(0, 193, 77, 0.15)", borderRadius: "16px", padding: "14px" }}>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: "24px", fontWeight: 700, color: "var(--success)" }}>{stats.approved}</div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "1px" }}>Validated</div>
            </div>
            <div style={{ flex: 1, textAlign: "center", background: "rgba(233, 80, 83, 0.06)", border: "1px solid rgba(233, 80, 83, 0.15)", borderRadius: "16px", padding: "14px" }}>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: "24px", fontWeight: 700, color: "var(--danger)" }}>{stats.rejected}</div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "1px" }}>Rejected</div>
            </div>
            <div style={{ flex: 1, textAlign: "center", background: "rgba(136, 56, 221, 0.06)", border: "1px solid rgba(136, 56, 221, 0.15)", borderRadius: "16px", padding: "14px" }}>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: "24px", fontWeight: 700, color: "var(--cyan)" }}>{stats.pending}</div>
              <div style={{ fontSize: "10px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "1px" }}>Pending Triage</div>
            </div>
          </div>
        </div>

        <div className="dashboard-card-box">
          <div style={{ fontSize: "13px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "1px", marginBottom: "16px", color: "var(--text-muted)" }}>
            Session Health
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {sessions.map((s) => {
              const isReady = s.state === "ready";
              return (
                <div key={s.platform} style={{ background: "var(--bg-inner)", border: "1px solid var(--border-subtle)", borderRadius: "11px", padding: "10px 13px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "11px" }}>
                    <span style={{ width: "9px", height: "9px", borderRadius: "50%", background: isReady ? "var(--success)" : "var(--danger)", boxShadow: isReady ? "0 0 8px var(--success)" : "none" }} />
                    <span style={{ fontSize: "13px", fontWeight: 600, flex: 1, color: "var(--text-main)" }}>{s.name}</span>
                    <span style={{ fontSize: "11px", fontWeight: 700, color: isReady ? "var(--success)" : "var(--danger)", fontFamily: "var(--font-mono)" }}>
                      {isReady ? "Active" : s.state}
                    </span>
                  </div>
                  <div style={{ fontSize: "10px", color: "var(--text-dim)", marginTop: "6px", paddingLeft: "20px" }}>
                    pool {s.pool_ready}/{s.pool_total} ready
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="dashboard-card-box">
        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "14px" }}>
          <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--success)", animation: "blink 2s infinite" }} />
          <span style={{ fontSize: "13px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "1px", color: "var(--text-muted)" }}>
            Live Activity Event Log
          </span>
        </div>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", display: "flex", flexDirection: "column", gap: "7px", maxHeight: "220px", overflowY: "auto" }}>
          {!logs.length && (
            <div style={{ color: "var(--text-dim)", padding: "10px 0" }}>
              No active job events logged yet. Launch a search from Home.
            </div>
          )}
          {logs.map((l, i) => (
            <div key={i} style={{ display: "flex", gap: "10px" }}>
              <span style={{ color: "var(--cyan)", minWidth: "90px" }}>[{l.type || "INFO"}]</span>
              <span style={{ color: "var(--text-main)", flex: 1 }}>{l.message || `Found: ${l.found ?? 0}`}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="dashboard-card-box">
        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "14px" }}>
          <span style={{ fontSize: "13px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "1px", color: "var(--text-muted)" }}>
            Incidents
          </span>
          <span style={{ fontSize: "10px", color: "var(--text-dim)", fontFamily: "var(--font-mono)" }}>auto-cleared after 14 days</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "10px", maxHeight: "320px", overflowY: "auto" }}>
          {!incidents.length && (
            <div style={{ color: "var(--text-dim)", fontSize: "12px", padding: "10px 0" }}>
              No job failures recorded. This fills in automatically the moment
              a discovery or analysis run actually breaks.
            </div>
          )}
          {incidents.map((inc) => (
            <div key={inc.id} style={{ background: "var(--bg-inner)", border: "1px solid var(--border-subtle)", borderRadius: "10px", padding: "10px 13px", fontSize: "12px" }}>
              <div style={{ display: "flex", gap: "8px", alignItems: "center", marginBottom: "6px" }}>
                <span style={{ fontSize: "10px", fontWeight: 700, color: "var(--danger)", background: "rgba(233, 80, 83,0.12)", padding: "2px 8px", borderRadius: "999px", textTransform: "uppercase" }}>
                  {inc.platform} · {inc.kind}
                </span>
                <span style={{ color: "var(--text-dim)", fontSize: "11px" }}>
                  {inc.scope} · {new Date(inc.ts).toLocaleString()}
                </span>
              </div>
              <div style={{ color: "var(--text-main)", marginBottom: "4px" }}>
                <b>Why:</b> {inc.cause}
              </div>
              <div style={{ color: "var(--cyan-bright)" }}>
                <b>Fix:</b> {inc.fix}
              </div>
              <details style={{ marginTop: "6px" }}>
                <summary style={{ cursor: "pointer", color: "var(--text-dim)", fontSize: "11px" }}>raw error</summary>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-dim)", marginTop: "4px" }}>
                  {inc.error_type}: {inc.message}
                </div>
              </details>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
