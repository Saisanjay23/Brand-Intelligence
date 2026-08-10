import { useEffect, useState } from "react";
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, BarChart, Bar, XAxis, YAxis } from "recharts";
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
  const [activeTab, setActiveTab] = useState<"overview" | "diagnostics">("overview");

  // One aggregate query instead of five separate limit=1 list_profiles
  // calls just to read back their `total` -- GET /profiles/stats exposes
  // the repository's own `stats()` (it already existed server-side, just
  // had no route). Per-platform distribution still needs one call per
  // platform since stats() isn't grouped by platform, but that's now the
  // only remaining fan-out, not six.
  useEffect(() => {
    if (!clientId) {
      setStats(EMPTY_STATS);
      setPlatformCounts({});
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const s = await profilesApi.stats(clientId);
        if (cancelled) return;
        setStats({
          total: s.total, analysed: s.analysed, approved: s.approved,
          rejected: s.rejected, pending: s.pending,
        });

        const perPlatform = await Promise.all(
          platforms.map((p) =>
            profilesApi.stats(clientId, p.platform).then((r) => [p.platform, r.total] as const),
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
        return "var(--cyan)";
    }
  };

  const pieData = [
    { name: "Validated", value: stats.approved, color: "var(--success)" },
    { name: "Rejected", value: stats.rejected, color: "var(--danger)" },
    { name: "Pending", value: stats.pending, color: "var(--cyan)" }
  ].filter(d => d.value > 0);

  const barData = platforms.map(p => ({
    name: p.name,
    count: platformCounts[p.platform] ?? 0,
    color: getPlatformColor(p.platform)
  }));

  return (
    <div style={{ animation: "fadeUp 0.4s ease" }}>
      <div style={{ display: "flex", gap: "12px", marginBottom: "20px", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "12px", alignItems: "center", flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={() => setActiveTab("overview")}
          style={{
            padding: "8px 16px",
            borderRadius: "8px",
            fontSize: "13px",
            fontWeight: 700,
            border: activeTab === "overview" ? "1px solid var(--cyan)" : "1px solid transparent",
            background: activeTab === "overview" ? "rgba(0, 229, 255, 0.12)" : "transparent",
            color: activeTab === "overview" ? "var(--cyan-bright)" : "var(--text-dim)",
            cursor: "pointer",
            transition: "all 0.2s ease",
          }}
        >
          📊 Overview & Activity
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("diagnostics")}
          style={{
            padding: "8px 16px",
            borderRadius: "8px",
            fontSize: "13px",
            fontWeight: 700,
            border: activeTab === "diagnostics" ? "1px solid var(--warn-yellow)" : "1px solid transparent",
            background: activeTab === "diagnostics" ? "rgba(255, 193, 7, 0.12)" : "transparent",
            color: activeTab === "diagnostics" ? "#FFD700" : "var(--text-dim)",
            cursor: "pointer",
            transition: "all 0.2s ease",
            display: "flex",
            alignItems: "center",
            gap: "6px",
          }}
        >
          ⚡ Platform Diagnostics & Scraper Health
        </button>
      </div>

      {activeTab === "overview" && (
        <>
          <div className="dashboard-grid">
            <div className="stat-tile" style={{ background: "linear-gradient(135deg, #8838DD, #9A50E9)", boxShadow: "0 8px 30px rgba(136, 56, 221, 0.25)" }}>
              <div className="stat-tile-num">{stats.analysed}</div>
              <div className="stat-tile-label">Analysed Profiles</div>
            </div>

            <div className="stat-tile" style={{ background: "linear-gradient(135deg, #2CA88E, #36B5A0)", boxShadow: "0 8px 30px rgba(54, 181, 160, 0.25)" }}>
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
            <div className="dashboard-card-box" style={{ display: 'flex', flexDirection: 'column' }}>
              <div style={{ fontSize: "13px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "1px", marginBottom: "16px", color: "var(--text-muted)" }}>
                Platform Distribution ({clientId || "no client set"})
              </div>
              <div style={{ flex: 1, minHeight: 250 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={barData} layout="vertical" margin={{ top: 5, right: 36, left: 10, bottom: 5 }}>
                    <XAxis type="number" hide />
                    <YAxis dataKey="name" type="category" width={90} axisLine={false} tickLine={false} tick={{ fill: 'var(--text-muted)', fontSize: 12, fontWeight: 600 }} />
                    <Tooltip cursor={{ fill: 'rgba(255,255,255,0.05)' }} contentStyle={{ background: 'rgba(16, 24, 40, 0.9)', border: '1px solid var(--border-subtle)', borderRadius: '8px', color: '#fff', fontSize: '12px' }} />
                    <Bar dataKey="count" radius={[0, 4, 4, 0]} label={{ position: 'right', fill: 'var(--text-muted)', fontSize: 11, fontWeight: 700 }}>
                      {barData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.color} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="dashboard-card-box">
              <div style={{ fontSize: "13px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "1px", marginBottom: "16px", color: "var(--text-muted)" }}>
                Risk Breakdown
              </div>
              <div style={{ display: 'flex', alignItems: 'center', height: '200px' }}>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={pieData.length > 0 ? pieData : [{ name: "No Data", value: 1, color: "var(--bg-inner)" }]}
                      cx="50%"
                      cy="50%"
                      innerRadius={65}
                      outerRadius={85}
                      paddingAngle={5}
                      dataKey="value"
                      stroke="none"
                    >
                      {(pieData.length > 0 ? pieData : [{ name: "No Data", value: 1, color: "var(--bg-inner)" }]).map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={{ background: 'rgba(16, 24, 40, 0.9)', border: '1px solid var(--border-subtle)', borderRadius: '8px', color: '#fff', fontSize: '12px' }} itemStyle={{ color: '#fff' }} />
                  </PieChart>
                </ResponsiveContainer>
              </div>

              <div style={{ display: "flex", gap: "12px", marginTop: "12px", paddingTop: "16px", borderTop: "1px solid var(--border-subtle)" }}>
                <div style={{ flex: 1, textAlign: "center", background: "rgba(54, 181, 160, 0.06)", border: "1px solid rgba(54, 181, 160, 0.15)", borderRadius: "12px", padding: "10px" }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: "18px", fontWeight: 700, color: "var(--success)" }}>{stats.approved}</div>
                  <div style={{ fontSize: "9px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "1px" }}>Validated</div>
                </div>
                <div style={{ flex: 1, textAlign: "center", background: "rgba(233, 80, 83, 0.06)", border: "1px solid rgba(233, 80, 83, 0.15)", borderRadius: "12px", padding: "10px" }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: "18px", fontWeight: 700, color: "var(--danger)" }}>{stats.rejected}</div>
                  <div style={{ fontSize: "9px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "1px" }}>Rejected</div>
                </div>
                <div style={{ flex: 1, textAlign: "center", background: "rgba(0, 229, 255, 0.06)", border: "1px solid rgba(0, 229, 255, 0.15)", borderRadius: "12px", padding: "10px" }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: "18px", fontWeight: 700, color: "var(--cyan)" }}>{stats.pending}</div>
                  <div style={{ fontSize: "9px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "1px" }}>Pending</div>
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
        </>
      )}

      {activeTab === "diagnostics" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "16px", animation: "fadeUp 0.3s ease" }}>
          <div className="dashboard-card-box" style={{ background: "linear-gradient(90deg, rgba(136,56,221,0.08), rgba(0,229,255,0.05))", borderLeft: "4px solid var(--cyan)" }}>
            <div style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-main)", marginBottom: "4px" }}>
              Live Platform Scraper Health & Architecture Diagnostics
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
              Monitor scraping limitations, session readiness, and runtime stability notes across all social media engines while running jobs without interrupting analysis tables.
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {platforms.map((p) => {
              const session = sessions.find((s) => s.platform === p.platform);
              const isStub = p.analysis_stub;
              const hasNote = !!p.stability_note;
              const statusColor = isStub ? "var(--danger)" : hasNote ? "var(--warn-yellow)" : "var(--success)";

              return (
                <div key={p.platform} className="dashboard-card-box" style={{ borderLeft: `4px solid ${statusColor}`, padding: "16px 20px" }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px", flexWrap: "wrap", gap: "10px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <span style={{ fontSize: "18px", fontWeight: 700, color: getPlatformColor(p.platform) }}>
                        ● {p.name}
                      </span>
                      <span style={{
                        fontSize: "11px", fontWeight: 700, textTransform: "uppercase",
                        padding: "3px 8px", borderRadius: "6px",
                        background: p.state === "healthy" ? "rgba(54,181,160,0.15)" : p.state === "critical" ? "rgba(233,80,83,0.15)" : "rgba(255,193,7,0.15)",
                        color: p.state === "healthy" ? "var(--success)" : p.state === "critical" ? "var(--danger)" : "var(--warn-yellow)",
                        fontFamily: "var(--font-mono)"
                      }}>
                        State: {p.state || "unknown"}
                      </span>
                      <span style={{ fontSize: "12px", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                        Health Score: {p.total ? `${p.score ?? 100}%` : "Not yet run"}
                      </span>
                    </div>
                    {session && (
                      <div style={{ fontSize: "11px", background: "var(--bg-inner)", padding: "6px 12px", borderRadius: "8px", border: "1px solid var(--border-subtle)", color: "var(--text-main)", fontFamily: "var(--font-mono)" }}>
                        Session: <b>{session.state}</b> · Ready Pool: <b>{session.pool_ready}/{session.pool_total}</b>
                      </div>
                    )}
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: "10px", marginBottom: "14px" }}>
                    <div style={{ background: "rgba(255,255,255,0.02)", padding: "8px 12px", borderRadius: "8px", fontSize: "11px" }}>
                      <div style={{ color: "var(--text-dim)", textTransform: "uppercase", fontSize: "10px" }}>Successful Scrapes</div>
                      <div style={{ fontFamily: "var(--font-mono)", fontSize: "16px", fontWeight: 700, color: "var(--success)", marginTop: "2px" }}>{p.ok || 0}</div>
                    </div>
                    <div style={{ background: "rgba(255,255,255,0.02)", padding: "8px 12px", borderRadius: "8px", fontSize: "11px" }}>
                      <div style={{ color: "var(--text-dim)", textTransform: "uppercase", fontSize: "10px" }}>Partial Scrapes</div>
                      <div style={{ fontFamily: "var(--font-mono)", fontSize: "16px", fontWeight: 700, color: "var(--warn-yellow)", marginTop: "2px" }}>{p.partial || 0}</div>
                    </div>
                    <div style={{ background: "rgba(255,255,255,0.02)", padding: "8px 12px", borderRadius: "8px", fontSize: "11px" }}>
                      <div style={{ color: "var(--text-dim)", textTransform: "uppercase", fontSize: "10px" }}>Failed Scrapes</div>
                      <div style={{ fontFamily: "var(--font-mono)", fontSize: "16px", fontWeight: 700, color: "var(--danger)", marginTop: "2px" }}>{p.bad || 0}</div>
                    </div>
                    <div style={{ background: "rgba(255,255,255,0.02)", padding: "8px 12px", borderRadius: "8px", fontSize: "11px" }}>
                      <div style={{ color: "var(--text-dim)", textTransform: "uppercase", fontSize: "10px" }}>Total Executions</div>
                      <div style={{ fontFamily: "var(--font-mono)", fontSize: "16px", fontWeight: 700, color: "var(--cyan-bright)", marginTop: "2px" }}>{p.total || 0}</div>
                    </div>
                  </div>

                  <div style={{
                    background: isStub ? "rgba(233,80,83,0.08)" : hasNote ? "rgba(255,193,7,0.08)" : "rgba(54,181,160,0.06)",
                    border: `1px solid ${isStub ? "rgba(233,80,83,0.25)" : hasNote ? "rgba(255,193,7,0.25)" : "rgba(54,181,160,0.2)"}`,
                    borderRadius: "10px", padding: "12px 14px", display: "flex", gap: "12px", alignItems: "flex-start", fontSize: "12px", color: "var(--text-main)"
                  }}>
                    <span style={{ fontSize: "18px" }}>{isStub ? "🚫" : hasNote ? "⚠️" : "✅"}</span>
                    <div>
                      <div style={{ fontWeight: 700, marginBottom: "3px", color: isStub ? "#f87171" : hasNote ? "#fbbf24" : "#34d399" }}>
                        {isStub ? "Scraping Capability: Limited (Discovery Only)" : hasNote ? "Scraper Architecture Notice & Fallbacks" : "Scraping Capability: Fully Supported"}
                      </div>
                      <div style={{ color: "var(--text-main)", lineHeight: "1.4" }}>
                        {p.stability_note || "Discovery and full analysis-phase field scraping are actively maintained and fully operational."}
                      </div>
                      {p.analysis_stub && (
                        <div style={{ color: "var(--text-dim)", marginTop: "4px", fontSize: "11px", borderTop: "1px dashed rgba(233,80,83,0.2)", paddingTop: "4px" }}>
                          * Running analysis for {p.name} won't produce scored results -- discovery and triage continue to function seamlessly.
                        </div>
                      )}
                    </div>
                  </div>

                  {p.last_error && (
                    <div style={{ marginTop: "12px", padding: "10px 12px", background: "rgba(233,80,83,0.06)", borderRadius: "8px", border: "1px solid rgba(233,80,83,0.15)", fontSize: "11px" }}>
                      <span style={{ fontWeight: 700, color: "var(--danger)" }}>Last Recorded Scraper Error: </span>
                      <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{p.last_error}</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <div className="dashboard-card-box">
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "14px" }}>
              <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--success)", animation: "blink 2s infinite" }} />
              <span style={{ fontSize: "13px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "1px", color: "var(--text-muted)" }}>
                Live Scraper Execution Logs
              </span>
            </div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", display: "flex", flexDirection: "column", gap: "7px", maxHeight: "250px", overflowY: "auto" }}>
              {!logs.length && (
                <div style={{ color: "var(--text-dim)", padding: "10px 0" }}>
                  No active job events logged yet. Launch a search or analysis job to observe real-time scraper events here.
                </div>
              )}
              {logs.map((l, i) => (
                <div key={i} style={{ display: "flex", gap: "10px", padding: "4px 0", borderBottom: "1px solid rgba(255,255,255,0.02)" }}>
                  <span style={{ color: "var(--cyan)", minWidth: "90px" }}>[{l.type || "INFO"}]</span>
                  <span style={{ color: "var(--text-main)", flex: 1 }}>{l.message || `Found: ${l.found ?? 0}`}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
