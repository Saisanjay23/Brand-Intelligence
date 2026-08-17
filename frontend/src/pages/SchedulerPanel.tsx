// Live view of the always-on round-robin engine (backend/services/
// round_robin_service.py), every client it cycles through, when it last
// ran, whether that run succeeded, and a rough ETA until it comes back
// around. Refreshed on an interval, not the 2s job-polling cadence used
// for a single in-flight job, this is a status table over ~hundreds of
// clients, not a live progress bar.
import { Fragment, useEffect, useState } from "react";
import { jobsApi } from "../api/jobsApi";
import type { JobEvent } from "../api/types";
import { schedulerApi, type SchedulerStatus } from "../api/schedulerApi";

const REFRESH_MS = 20_000;
const EVENTS_REFRESH_MS = 3_000;

// The analyst's whole point of expanding a row is to watch this client's
// current run without ever opening the actual platform, so poll its
// event stream while expanded, same idea as useJobPolling but scoped to
// "whatever this client's most recent job is" rather than one known job id.
function ClientEventLog({ clientId }: { clientId: string }) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [jobStatus, setJobStatus] = useState<string>("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let lastSeq = 0;
    let jobId = "";

    const poll = async () => {
      try {
        if (!jobId) {
          const { items } = await jobsApi.jobs(clientId, 1);
          if (!items.length) {
            if (!cancelled) setError("No jobs recorded yet for this client.");
            return;
          }
          jobId = items[0].id;
          setJobStatus(items[0].status);
        }
        const { items: newEvents } = await jobsApi.jobEvents(jobId, lastSeq);
        if (cancelled) return;
        if (newEvents.length) {
          lastSeq = newEvents[newEvents.length - 1].seq;
          setEvents((prev) => [...prev, ...newEvents].slice(-100));
        }
        const job = await jobsApi.job(jobId);
        if (!cancelled) setJobStatus(job.status);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    };

    poll();
    const t = setInterval(poll, EVENTS_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [clientId]);

  return (
    <div style={{ padding: "12px 16px", background: "var(--bg-inner)", borderRadius: "8px", fontFamily: "var(--font-mono)", fontSize: "12px" }}>
      {jobStatus && <div style={{ marginBottom: "6px", color: "var(--text-dim)" }}>current job status: <strong style={{ color: "var(--text-main)" }}>{jobStatus}</strong></div>}
      {error && <div style={{ color: "var(--text-dim)" }}>{error}</div>}
      {!error && events.length === 0 && <div style={{ color: "var(--text-dim)" }}>waiting for activity…</div>}
      <div style={{ maxHeight: "180px", overflowY: "auto", display: "flex", flexDirection: "column", gap: "2px" }}>
        {events.map((e) => (
          <div key={e.seq} style={{ color: "var(--text-muted)" }}>
            [{e.type}] {e.message}
          </div>
        ))}
      </div>
    </div>
  );
}

function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "just now";
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ${mins % 60}m ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

// The full, unambiguous local timestamp, shown as a tooltip (and in the
// "running now" strip) next to the relative time, so "23m ago" is never the
// only answer to "when exactly did this run".
function exactTime(iso: string | null): string {
  if (!iso) return "never run yet";
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function durationLabel(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m ${secs}s`;
}

// Counts UP from a fixed start instant, for "running for 1m 12s" on a
// still-in-progress turn, `nowMs` is a locally-ticking clock (see
// `useNowTick` below), not a fresh network read, so this updates every
// second without polling the server that often.
function elapsedLabel(sinceIso: string | null, nowMs: number): string {
  if (!sinceIso) return "";
  const secs = Math.max(0, Math.floor((nowMs - new Date(sinceIso).getTime()) / 1000));
  return durationLabel(secs);
}

function etaLabel(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return "< 1m";
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `~${mins}m`;
  return `~${(mins / 60).toFixed(1)}h`;
}

const STATUS_LOOK: Record<string, { icon: string; color: string; label: string }> = {
  success: { icon: "●", color: "var(--success, #36b5a0)", label: "success" },
  failed: { icon: "●", color: "var(--danger, #e95053)", label: "failed" },
  skipped: { icon: "●", color: "var(--warn-yellow, #fdb71b)", label: "skipped" },
};

const PHASE_LOOK: Record<string, string> = {
  checking: "checking sessions",
  discovery: "running discovery",
  analysis: "analysing approved profiles",
};

// A 1s local tick so "running for Xs" counts up live between the panel's
// own 20s network refreshes, without polling the server any harder.
function useNowTick(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

export function SchedulerPanel() {
  const [status, setStatus] = useState<SchedulerStatus | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [toggling, setToggling] = useState(false);
  const [togglingAutostart, setTogglingAutostart] = useState(false);
  const [expanded, setExpanded] = useState<string>("");

  const load = () => {
    schedulerApi
      .status()
      .then(setStatus)
      .catch((e) => setError((e as Error).message));
  };

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, []);

  const toggleEngine = async () => {
    if (!status) return;
    setToggling(true);
    setError("");
    try {
      const next = status.running ? await schedulerApi.stop() : await schedulerApi.start();
      setStatus(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setToggling(false);
    }
  };

  // Separate from toggleEngine on purpose: this only decides what happens
  // the NEXT time the server process boots (see main.py's lifespan), it
  // does not start or stop anything right now, so it must never touch
  // `status.running` itself, only `status.autostart`.
  const toggleAutostart = async () => {
    if (!status) return;
    setTogglingAutostart(true);
    setError("");
    try {
      const { autostart } = await schedulerApi.setAutostart(!status.autostart);
      setStatus({ ...status, autostart });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setTogglingAutostart(false);
    }
  };

  const now = useNowTick();

  const clients = (status?.clients ?? []).filter((c) =>
    !filter.trim() || c.name.toLowerCase().includes(filter.trim().toLowerCase()),
  );
  const runningNow = clients.filter((c) => c.current_phase);

  return (
    <div style={{ padding: "24px", color: "var(--text-main, #f2f4f7)", maxWidth: "1100px", margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "20px", flexWrap: "wrap", gap: "12px" }}>
        <div>
          <h1 style={{ fontSize: "22px", fontWeight: 700, color: "var(--text-primary, #fff)", margin: 0, letterSpacing: "-0.3px" }}>
            🔁 Scheduler
          </h1>
          <p style={{ fontSize: "13px", color: "var(--text-muted, #98a2b3)", margin: "4px 0 0 0" }}>
            The engine that runs every client automatically all day, in rotation -- discovery, then
            picking up newly-approved profiles for analysis, before moving on to the next client.
          </p>
        </div>
        {status && (
          <div style={{ display: "flex", alignItems: "center", gap: "14px", flexWrap: "wrap" }}>
            <label
              title="When ON, this engine starts itself automatically the moment the server process boots. When OFF, it stays paused after a restart until someone clicks Resume below -- it never starts itself in the background."
              style={{
                display: "flex", alignItems: "center", gap: "8px", cursor: togglingAutostart ? "wait" : "pointer",
                fontSize: "13px", color: "var(--text-muted, #98a2b3)", userSelect: "none",
              }}
            >
              <span>Auto-start on boot</span>
              <span
                onClick={toggleAutostart}
                role="switch"
                aria-checked={status.autostart}
                style={{
                  position: "relative", width: "36px", height: "20px", borderRadius: "999px",
                  background: status.autostart ? "rgba(54,181,160,0.4)" : "rgba(152,162,179,0.3)",
                  transition: "background 0.2s", opacity: togglingAutostart ? 0.6 : 1,
                }}
              >
                <span
                  style={{
                    position: "absolute", top: "2px", left: status.autostart ? "18px" : "2px",
                    width: "16px", height: "16px", borderRadius: "50%", background: "#fff",
                    transition: "left 0.2s",
                  }}
                />
              </span>
            </label>
            <button
              onClick={toggleEngine}
              disabled={toggling}
              style={{
                padding: "10px 18px", borderRadius: "10px", cursor: toggling ? "wait" : "pointer",
                background: status.running ? "rgba(233,80,83,0.12)" : "rgba(54,181,160,0.12)",
                border: `1px solid ${status.running ? "rgba(233,80,83,0.4)" : "rgba(54,181,160,0.4)"}`,
                color: status.running ? "var(--danger)" : "var(--success)",
                fontSize: "13px", fontWeight: 700, whiteSpace: "nowrap",
              }}
            >
              {status.running ? "⏸ Pause engine" : "▶ Resume engine"}
            </button>
          </div>
        )}
      </div>

      {error && (
        <div style={{
          padding: "10px 16px", background: "rgba(233, 80, 83,0.1)", border: "1px solid rgba(233, 80, 83,0.25)",
          color: "var(--danger)", borderRadius: "10px", marginBottom: "16px", fontSize: "13px",
        }}>
          ⚠️ {error}
        </div>
      )}

      {status && status.consecutive_failures >= 3 && (
        <div style={{
          padding: "10px 16px", background: "rgba(255,193,7,0.1)", border: "1px solid rgba(255,193,7,0.3)",
          color: "var(--warn-yellow, #fdb71b)", borderRadius: "10px", marginBottom: "16px", fontSize: "13px",
        }}>
          ⚠️ {status.consecutive_failures} client turns in a row have failed. The engine is backing off
          automatically between attempts. This usually means something systemic (not one client's own
          keywords/session) -- check the Incidents list, or an admin email if Mail alerts are configured.
        </div>
      )}

      {status && (
        <div style={{ display: "flex", gap: "12px", marginBottom: "20px", flexWrap: "wrap" }}>
          <div style={{ flex: "1 1 140px", background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: "12px", padding: "12px 16px" }}>
            <div style={{ fontSize: "10px", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "1px" }}>Engine</div>
            <div style={{ fontSize: "16px", fontWeight: 700, color: status.running ? "var(--success)" : "var(--text-dim)", marginTop: "2px" }}>
              {status.running ? "● Running" : "○ Paused"}
            </div>
          </div>
          <div style={{ flex: "1 1 140px", background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: "12px", padding: "12px 16px" }}>
            <div style={{ fontSize: "10px", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "1px" }}>Concurrent slots</div>
            <div style={{ fontSize: "16px", fontWeight: 700, color: "var(--cyan-bright)", marginTop: "2px" }}>
              {runningNow.length}/{status.slots} busy
            </div>
          </div>
          <div style={{ flex: "1 1 140px", background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: "12px", padding: "12px 16px" }}>
            <div style={{ fontSize: "10px", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "1px" }}>Avg. time / client</div>
            <div style={{ fontSize: "16px", fontWeight: 700, color: "var(--text-main)", marginTop: "2px" }}>{Math.round(status.avg_duration_seconds)}s</div>
          </div>
          <div style={{ flex: "1 1 140px", background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: "12px", padding: "12px 16px" }}>
            <div style={{ fontSize: "10px", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "1px" }}>Clients in rotation</div>
            <div style={{ fontSize: "16px", fontWeight: 700, color: "var(--text-main)", marginTop: "2px" }}>{clients.length}</div>
          </div>
        </div>
      )}

      {/* One line per busy slot, the direct, unambiguous answer to
          "which client is running right now", instead of making an admin
          scan the whole table for a badge. Empty (not rendered) the moment
          every slot goes idle, so it never sits there stale. */}
      {status && status.running && runningNow.length > 0 && (
        <div style={{
          marginBottom: "16px", background: "rgba(124, 92, 255, 0.08)",
          border: "1px solid var(--accent, #7c5cff)", borderRadius: "12px", padding: "10px 16px",
        }}>
          <div style={{ fontSize: "10px", color: "var(--accent, #7c5cff)", textTransform: "uppercase", letterSpacing: "1px", fontWeight: 700, marginBottom: "6px" }}>
            ● Running now
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            {runningNow.map((c) => (
              <div key={c.client_id} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "13px" }}>
                <span style={{
                  width: "6px", height: "6px", borderRadius: "50%", background: "var(--accent, #7c5cff)",
                  animation: "pulse 1.2s ease-in-out infinite", flexShrink: 0,
                }} />
                <strong style={{ color: "var(--text-primary, #fff)" }}>{c.name}</strong>
                <span style={{ color: "var(--text-muted, #98a2b3)" }}>
                  {c.current_phase ? PHASE_LOOK[c.current_phase] ?? c.current_phase : ""}
                </span>
                <span style={{ color: "var(--text-dim)", fontSize: "12px" }}>
                  — running for {elapsedLabel(c.current_since, now)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="🔎 Filter by client name…"
        style={{
          width: "100%", marginBottom: "12px", background: "var(--bg-inner)", border: "1px solid var(--border-color)",
          borderRadius: "10px", padding: "10px 12px", color: "var(--text-main)", fontSize: "13px", outline: "none",
        }}
      />

      <div style={{ overflowX: "auto" }}>
        <table className="core_table">
          <thead>
            <tr>
              <th>Client</th>
              <th>Status</th>
              <th>Last run</th>
              <th>Duration</th>
              <th>Runs</th>
              <th>Next run</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {!status ? (
              <tr><td colSpan={7} style={{ textAlign: "center", padding: "24px", color: "var(--text-dim)" }}>Loading…</td></tr>
            ) : clients.length === 0 ? (
              <tr><td colSpan={7} style={{ textAlign: "center", padding: "24px", color: "var(--text-dim)" }}>No clients with keywords set yet.</td></tr>
            ) : (
              clients.map((c) => {
                const look = c.last_run_status ? STATUS_LOOK[c.last_run_status] : null;
                const isOpen = expanded === c.client_id;
                const running = !!c.current_phase;
                return (
                  <Fragment key={c.client_id}>
                    <tr
                      onClick={() => setExpanded(isOpen ? "" : c.client_id)}
                      style={{ cursor: "pointer", background: running ? "rgba(124, 92, 255, 0.06)" : undefined }}
                      title="Click to see this client's live progress"
                    >
                      <td>{isOpen ? "▾" : "▸"} {c.name}</td>
                      <td>
                        {running ? (
                          <span style={{ color: "var(--accent, #7c5cff)", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: "6px" }}>
                            <span style={{
                              width: "6px", height: "6px", borderRadius: "50%", background: "var(--accent, #7c5cff)",
                              animation: "pulse 1.2s ease-in-out infinite",
                            }} />
                            running — {PHASE_LOOK[c.current_phase ?? ""] ?? c.current_phase} ({elapsedLabel(c.current_since, now)})
                          </span>
                        ) : look ? (
                          <span style={{ color: look.color, fontWeight: 600 }}>{look.icon} {look.label}</span>
                        ) : (
                          <span style={{ color: "var(--text-dim)" }}>— not yet run</span>
                        )}
                      </td>
                      <td title={exactTime(c.last_run_at)}>{relativeTime(c.last_run_at)}</td>
                      <td>{durationLabel(c.last_run_duration_s)}</td>
                      <td title="Total completed turns through the round-robin rotation">{c.run_count}×</td>
                      <td>{running ? "—" : status.running ? etaLabel(c.eta_seconds) : "paused"}</td>
                      <td style={{ color: "var(--text-dim)", fontSize: "12px", maxWidth: "260px" }}>{c.last_run_note || "—"}</td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={7} style={{ padding: "0 0 12px" }}>
                          <ClientEventLog clientId={c.client_id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
