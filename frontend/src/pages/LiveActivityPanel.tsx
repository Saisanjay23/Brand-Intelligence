// Live Activity: everything a discovery/analysis job is doing right now,
// across every client, which platform is being scraped and for how long,
// which keywords/URLs are done vs up next, a Stop button per job, and a
// browser to inspect/delete the profile records those jobs produced.
//
// Deliberately polls the SAME `GET /jobs` list the rest of the app already
// uses (backend/api/job_routes.py) rather than opening anything new:
// job.to_dict() already carries the full per-platform breakdown
// (job_service.py's Job.platform_progress), so one list poll is the whole
// live view; no per-job event-stream subscription needed for this tab.
import { useEffect, useMemo, useRef, useState } from "react";
import { clientsApi } from "../api/clientsApi";
import { jobsApi } from "../api/jobsApi";
import { profilesApi } from "../api/profilesApi";
import type { Client, Job, JobEvent, PlatformProgress, Profile } from "../api/types";
import { confirmAction } from "../utils/confirmAction";
import { download } from "../utils/download";

const JOBS_REFRESH_MS = 4_000;
const LOG_REFRESH_MS = 2_000;
const BROWSE_PAGE_SIZE = 50;

function useNowTick(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function durationLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return mins < 60 ? `${mins}m ${secs}s` : `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

function exactTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  return hours < 24 ? `${hours}h ago` : `${Math.floor(hours / 24)}d ago`;
}

const JOB_STATUS_LOOK: Record<string, { color: string; label: string }> = {
  queued: { color: "var(--text-dim, #667085)", label: "queued" },
  running: { color: "var(--accent, #7c5cff)", label: "running" },
  done: { color: "var(--success, #36b5a0)", label: "done" },
  failed: { color: "var(--danger, #e95053)", label: "failed" },
  cancelled: { color: "var(--warn-yellow, #fdb71b)", label: "cancelled" },
};

const PLATFORM_STATUS_LOOK: Record<string, string> = {
  pending: "var(--text-dim, #667085)",
  running: "var(--accent, #7c5cff)",
  done: "var(--success, #36b5a0)",
  partial: "var(--warn-yellow, #fdb71b)",
  failed: "var(--danger, #e95053)",
  skipped: "var(--text-dim, #667085)",
};

function Badge({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: "5px", color, fontWeight: 700,
      fontSize: "11px", textTransform: "uppercase", letterSpacing: "0.4px",
    }}>
      <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: color, flexShrink: 0 }} />
      {children}
    </span>
  );
}

function ItemPreview({
  label, items, overflow, tone,
}: { label: string; items: string[] | null | undefined; overflow?: number | null; tone: string }) {
  // Defensive against a backend that hasn't picked up these fields yet
  // (they're new, a running process started before this shipped simply
  // won't send them). Missing data here must degrade to "nothing to show",
  // never crash the whole page, a single bad prop shape used to take
  // down this entire tab with no error boundary to catch it.
  const list = items ?? [];
  const extra = overflow ?? 0;
  if (!list.length && !extra) return null;
  return (
    <div style={{ fontSize: "11px", color: "var(--text-dim, #98a2b3)" }}>
      <span style={{ fontWeight: 600, color: tone }}>{label}</span>{" "}
      {list.length ? (
        <span title={list.join(", ")}>
          {list.slice(-6).join(", ")}
          {extra ? ` … +${extra} more` : ""}
        </span>
      ) : (
        <span>—</span>
      )}
    </div>
  );
}

function PlatformRow({ pid, p, now }: { pid: string; p: PlatformProgress; now: number }) {
  const pct = p.total > 0 ? Math.min(100, Math.round((p.processed / p.total) * 100)) : 0;
  const color = PLATFORM_STATUS_LOOK[p.status] ?? "var(--text-dim)";
  const liveElapsed = p.started
    ? p.status === "running"
      ? Math.floor((now - p.started * 1000) / 1000)
      : p.elapsed_seconds
    : null;
  return (
    <div style={{
      border: "1px solid var(--border-subtle, rgba(255,255,255,0.08))", borderRadius: "8px",
      padding: "8px 10px", background: "var(--bg-inner, rgba(255,255,255,0.02))",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "6px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <strong style={{ fontSize: "12px", color: "var(--text-primary, #fff)", textTransform: "capitalize" }}>{pid}</strong>
          <Badge color={color}>{p.status}</Badge>
        </div>
        <div style={{ display: "flex", gap: "12px", fontSize: "11px", color: "var(--text-dim, #98a2b3)" }}>
          <span title="Wall-clock time this platform has taken">⏱ {durationLabel(liveElapsed)}</span>
          {p.status === "running" && p.eta_seconds !== null && <span title="Rough estimate to finish">ETA ~{durationLabel(p.eta_seconds)}</span>}
        </div>
      </div>

      {p.total > 0 && (
        <div style={{ margin: "6px 0 4px" }}>
          <div style={{ height: "5px", borderRadius: "3px", background: "rgba(255,255,255,0.08)", overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${pct}%`, background: color, transition: "width 0.4s ease" }} />
          </div>
          <div style={{ fontSize: "10px", color: "var(--text-dim, #98a2b3)", marginTop: "2px" }}>
            {p.processed} / {p.total} done ({pct}%)
          </div>
        </div>
      )}

      <ItemPreview label="Just finished:" items={p.done_items} tone="var(--success, #36b5a0)" />
      <ItemPreview label="Up next:" items={p.pending_items} overflow={p.pending_overflow} tone="var(--text-muted, #98a2b3)" />
    </div>
  );
}

// The actual scrolling log line-by-line, per job, straight off
// `GET /jobs/{id}/events`, which reads `job.events` (job_service.py's
// Event ring). That list lives ONLY on the in-memory Job object for as
// long as the job is tracked (capped at MAX_EVENTS_PER_JOB, and the whole
// Job is dropped once MAX_JOBS_IN_MEMORY is exceeded), nothing here is
// ever written to Mongo/any database; closing this tab (or the backend
// restarting) is the only way this history goes away.
function JobLiveLog({ jobId }: { jobId: string }) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [filterText, setFilterText] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const lastSeq = useRef(0);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setEvents([]);
    lastSeq.current = 0;
    const poll = () => {
      jobsApi.jobEvents(jobId, lastSeq.current).then((r) => {
        if (cancelled || !r.items.length) return;
        lastSeq.current = r.items[r.items.length - 1].seq;
        setEvents((prev) => [...prev, ...r.items].slice(-500));
      }).catch(() => {});
    };
    poll();
    const t = setInterval(poll, LOG_REFRESH_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, [jobId]);

  useEffect(() => {
    if (autoScroll && boxRef.current) {
      boxRef.current.scrollTop = boxRef.current.scrollHeight;
    }
  }, [events, autoScroll]);

  const filteredEvents = useMemo(() => {
    if (!filterText.trim()) return events;
    const q = filterText.toLowerCase();
    return events.filter((e) => e.message.toLowerCase().includes(q) || e.type.toLowerCase().includes(q));
  }, [events, filterText]);

  const handleDownloadLog = () => {
    const text = events.map((e) => `[${e.ts || new Date().toISOString()}] [${e.type.toUpperCase()}] ${e.message}`).join("\n");
    download(`job-${jobId}-log.txt`, text, "text/plain");
  };

  return (
    <div style={{ marginTop: "10px" }}>
      <div className="job-log-toolbar">
        <input
          type="text"
          className="job-log-search-input"
          placeholder="🔍 Filter logs..."
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
        />
        <div style={{ display: "flex", gap: "6px" }}>
          <button
            type="button"
            className={`job-log-action-btn ${autoScroll ? "active" : ""}`}
            onClick={() => setAutoScroll((v) => !v)}
            title="Toggle automatic scrolling to latest log line"
          >
            {autoScroll ? "🔒 Auto-scroll ON" : "🔓 Auto-scroll OFF"}
          </button>
          <button
            type="button"
            className="job-log-action-btn"
            onClick={handleDownloadLog}
            disabled={!events.length}
            title="Download full job event log as text file"
          >
            📥 Download Log
          </button>
        </div>
      </div>
      <div
        ref={boxRef}
        style={{
          maxHeight: "220px", overflowY: "auto", background: "var(--bg-inner, #0b1220)",
          border: "1px solid var(--border-subtle, rgba(255,255,255,0.08))", borderRadius: "8px",
          padding: "8px 10px", fontFamily: "var(--font-mono)", fontSize: "11px",
        }}
      >
        {filteredEvents.length === 0 ? (
          <div style={{ color: "var(--text-dim)" }}>
            {events.length === 0 ? "waiting for activity…" : "No log lines match the filter."}
          </div>
        ) : (
          filteredEvents.map((e) => (
            <div key={e.seq} style={{ color: e.type === "failed" ? "var(--danger, #e95053)" : "var(--text-muted, #98a2b3)" }}>
              <span style={{ color: "var(--text-dim)" }}>[{e.type}]</span> {e.message}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function JobCard({
  job, clientName, now, onStop, onBrowse, stopping, expanded, onToggleExpand,
}: {
  job: Job; clientName: string; now: number; onStop: (id: string) => void;
  onBrowse: (clientId: string, platform: string | null) => void; stopping: boolean;
  expanded: boolean; onToggleExpand: () => void;
}) {
  const look = JOB_STATUS_LOOK[job.status] ?? { color: "var(--text-dim)", label: job.status };
  const elapsed = job.started ? Math.floor((now - new Date(job.started).getTime()) / 1000) : null;
  const platformIds = Object.keys(job.platforms || {});
  const canStop = job.status === "queued" || job.status === "running";

  return (
    <div style={{
      background: "var(--bg-surface, #101828)", border: "1px solid var(--border-subtle, rgba(255,255,255,0.08))",
      borderRadius: "12px", padding: "14px 16px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "10px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{ fontSize: "16px" }}>{job.kind === "discovery" ? "🔍" : "🧪"}</span>
            <strong style={{ fontSize: "14px", color: "var(--text-primary, #fff)" }}>{clientName}</strong>
            <span style={{ fontSize: "11px", color: "var(--text-dim, #98a2b3)", textTransform: "capitalize" }}>{job.kind}</span>
            <Badge color={look.color}>{look.label}</Badge>
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-dim, #98a2b3)", marginTop: "3px" }} title={exactTime(job.started)}>
            started {relativeTime(job.started)} · running for {durationLabel(elapsed)}
            {job.blocked_by && (
              <span style={{ color: "var(--warn-yellow, #fdb71b)" }}>
                {" "}· waiting on {job.blocked_by.client_id}'s {job.blocked_by.kind}
              </span>
            )}
          </div>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button
            onClick={onToggleExpand}
            style={{
              padding: "6px 12px", borderRadius: "8px",
              background: expanded ? "rgba(124,92,255,0.15)" : "var(--bg-surface-3, #1d2939)",
              border: `1px solid ${expanded ? "var(--accent, #7c5cff)" : "var(--border-subtle, rgba(255,255,255,0.1))"}`,
              color: expanded ? "var(--accent, #7c5cff)" : "var(--text-body, #fff)",
              fontSize: "12px", fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap",
            }}
            title="Show/hide this job's live scrolling log"
          >
            {expanded ? "▾" : "▸"} Live log
          </button>
          <button
            onClick={() => onBrowse(job.client_id, job.platform)}
            style={{
              padding: "6px 12px", borderRadius: "8px", background: "var(--bg-surface-3, #1d2939)",
              border: "1px solid var(--border-subtle, rgba(255,255,255,0.1))", color: "var(--text-body, #fff)",
              fontSize: "12px", fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap",
            }}
            title="Jump to this client's results below"
          >
            View results
          </button>
          {canStop && (
            <button
              onClick={() => onStop(job.id)}
              disabled={stopping}
              style={{
                padding: "6px 12px", borderRadius: "8px", background: "rgba(233,80,83,0.12)",
                border: "1px solid rgba(233,80,83,0.4)", color: "var(--danger, #e95053)",
                fontSize: "12px", fontWeight: 700, cursor: stopping ? "wait" : "pointer", whiteSpace: "nowrap",
              }}
            >
              ⏹ Stop
            </button>
          )}
        </div>
      </div>

      {platformIds.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "12px" }}>
          {platformIds.map((pid) => (
            <PlatformRow key={pid} pid={pid} p={job.platforms[pid]} now={now} />
          ))}
        </div>
      )}
      {job.message && (
        <div style={{ marginTop: "8px", fontSize: "11px", color: "var(--text-dim, #98a2b3)", fontFamily: "var(--font-mono)" }}>
          {job.message}
        </div>
      )}
      {expanded && <JobLiveLog jobId={job.id} />}
    </div>
  );
}

const STATUS_BADGE: Record<string, string> = {
  pending: "var(--warn-yellow, #fdb71b)", approved: "var(--success, #36b5a0)", rejected: "var(--danger, #e95053)",
};

export function LiveActivityPanel() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [clients, setClients] = useState<Client[]>([]);
  const [error, setError] = useState("");
  const [stoppingId, setStoppingId] = useState<string>("");
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const now = useNowTick();

  const toggleExpand = (jobId: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      next.has(jobId) ? next.delete(jobId) : next.add(jobId);
      return next;
    });
  };

  // Browse & manage section
  const [browseClientId, setBrowseClientId] = useState("");
  const [browsePlatform, setBrowsePlatform] = useState("");
  const [browseStatus, setBrowseStatus] = useState("");
  const [browseSearch, setBrowseSearch] = useState("");
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profilesTotal, setProfilesTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [browseLoading, setBrowseLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    clientsApi.listClients().then((r) => setClients(r.items)).catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      jobsApi.jobs("", 100).then((r) => {
        if (!cancelled) setJobs(r.items);
      }).catch((e) => !cancelled && setError((e as Error).message));
    };
    load();
    const t = setInterval(load, JOBS_REFRESH_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const clientName = useMemo(() => {
    const m = new Map(clients.map((c) => [c.client_id, c.name || c.client_id]));
    return (id: string) => m.get(id) || id;
  }, [clients]);

  const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const recentTerminal = jobs
    .filter((j) => j.status === "done" || j.status === "failed" || j.status === "cancelled")
    .slice(0, 8);

  const stop = async (jobId: string) => {
    if (!(await confirmAction("Stop this job now? Whatever it hasn't finished scraping/analysing will be left as-is."))) return;
    setStoppingId(jobId);
    try {
      await jobsApi.cancelJob(jobId);
      const r = await jobsApi.jobs("", 100);
      setJobs(r.items);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStoppingId("");
    }
  };

  const browseTo = (clientId: string, platform: string | null) => {
    setBrowseClientId(clientId);
    setBrowsePlatform(platform || "");
    setOffset(0);
    document.getElementById("live-activity-browse")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const loadProfiles = () => {
    if (!browseClientId) {
      setProfiles([]); setProfilesTotal(0);
      return;
    }
    setBrowseLoading(true);
    profilesApi.profiles({
      client_id: browseClientId, platform: browsePlatform || undefined,
      status: browseStatus || undefined, search: browseSearch || undefined,
      limit: BROWSE_PAGE_SIZE, offset,
    })
      .then((r) => { setProfiles(r.items); setProfilesTotal(r.total); setSelected(new Set()); })
      .catch((e) => setError((e as Error).message))
      .finally(() => setBrowseLoading(false));
  };

  useEffect(loadProfiles, [browseClientId, browsePlatform, browseStatus, offset]);

  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) => (prev.size === profiles.length ? new Set() : new Set(profiles.map((p) => p.id))));
  };

  const deleteSelected = async () => {
    if (selected.size === 0) return;
    if (!(await confirmAction(`Permanently delete ${selected.size} profile record(s)? This cannot be undone.`))) return;
    setDeleting(true);
    try {
      await profilesApi.deleteProfiles(Array.from(selected));
      loadProfiles();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div style={{ color: "var(--text-main, #f2f4f7)" }}>
      <div style={{ marginBottom: "16px" }}>
        <h2 style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-primary, #fff)", margin: 0 }}>⚡ Live Activity</h2>
        <p style={{ fontSize: "13px", color: "var(--text-muted, #98a2b3)", margin: "4px 0 0 0" }}>
          Every discovery/analysis job in flight right now -- which platform, which keywords/URLs are done vs.
          up next, and how long each has taken. Stop a job here, or jump straight to its client's results below.
        </p>
      </div>

      {error && (
        <div style={{
          padding: "10px 16px", background: "rgba(233,80,83,0.1)", border: "1px solid rgba(233,80,83,0.25)",
          color: "var(--danger)", borderRadius: "10px", marginBottom: "16px", fontSize: "13px",
        }}>
          ⚠️ {error}
        </div>
      )}

      {active.length === 0 ? (
        <div style={{
          padding: "24px", textAlign: "center", color: "var(--text-dim)", background: "var(--bg-surface, #101828)",
          border: "1px dashed var(--border-subtle, rgba(255,255,255,0.1))", borderRadius: "12px", marginBottom: "20px",
        }}>
          Nothing running right now -- the round-robin engine or a manual sweep will show up here the moment it starts.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px", marginBottom: "24px" }}>
          {active.map((job) => (
            <JobCard
              key={job.id} job={job} clientName={clientName(job.client_id)} now={now}
              onStop={stop} onBrowse={browseTo} stopping={stoppingId === job.id}
              expanded={expandedIds.has(job.id)} onToggleExpand={() => toggleExpand(job.id)}
            />
          ))}
        </div>
      )}

      {recentTerminal.length > 0 && (
        <details style={{ marginBottom: "24px" }}>
          <summary style={{ cursor: "pointer", fontSize: "12px", color: "var(--text-dim, #98a2b3)", fontWeight: 600 }}>
            Recently finished ({recentTerminal.length})
          </summary>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "8px" }}>
            {recentTerminal.map((job) => {
              const look = JOB_STATUS_LOOK[job.status] ?? { color: "var(--text-dim)", label: job.status };
              const took = job.started && job.finished
                ? Math.round((new Date(job.finished).getTime() - new Date(job.started).getTime()) / 1000)
                : null;
              return (
                <div
                  key={job.id}
                  onClick={() => browseTo(job.client_id, job.platform)}
                  style={{
                    display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer",
                    padding: "8px 12px", background: "var(--bg-surface, #101828)", borderRadius: "8px",
                    border: "1px solid var(--border-subtle, rgba(255,255,255,0.06))", fontSize: "12px",
                  }}
                >
                  <span>
                    <strong style={{ color: "var(--text-primary, #fff)" }}>{clientName(job.client_id)}</strong>{" "}
                    <span style={{ color: "var(--text-dim)", textTransform: "capitalize" }}>{job.kind}</span>
                  </span>
                  <span style={{ display: "flex", gap: "12px", alignItems: "center" }}>
                    <Badge color={look.color}>{look.label}</Badge>
                    <span style={{ color: "var(--text-dim)" }} title={exactTime(job.finished)}>
                      took {durationLabel(took)} · {relativeTime(job.finished)}
                    </span>
                  </span>
                </div>
              );
            })}
          </div>
        </details>
      )}

      <div id="live-activity-browse" style={{ borderTop: "1px solid var(--border-subtle, rgba(255,255,255,0.08))", paddingTop: "20px" }}>
        <h3 style={{ fontSize: "15px", fontWeight: 700, color: "var(--text-primary, #fff)", margin: "0 0 4px 0" }}>
          🗄 Browse &amp; manage results
        </h3>
        <p style={{ fontSize: "12px", color: "var(--text-muted, #98a2b3)", margin: "0 0 12px 0" }}>
          Inspect what a job actually saved to the database, and permanently delete records if needed.
        </p>

        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "12px" }}>
          <select
            value={browseClientId}
            onChange={(e) => { setBrowseClientId(e.target.value); setOffset(0); }}
            style={selectStyle}
          >
            <option value="">Select a client…</option>
            {clients.map((c) => (
              <option key={c.client_id} value={c.client_id}>{c.name || c.client_id}</option>
            ))}
          </select>
          <select value={browsePlatform} onChange={(e) => { setBrowsePlatform(e.target.value); setOffset(0); }} style={selectStyle}>
            <option value="">All platforms</option>
            {["facebook", "instagram", "twitter", "youtube", "telegram", "tiktok"].map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <select value={browseStatus} onChange={(e) => { setBrowseStatus(e.target.value); setOffset(0); }} style={selectStyle}>
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
          </select>
          <input
            value={browseSearch}
            onChange={(e) => setBrowseSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && (setOffset(0), loadProfiles())}
            placeholder="🔎 Search name/URL…"
            style={{ ...selectStyle, flex: "1 1 200px" }}
          />
          <button onClick={() => { setOffset(0); loadProfiles(); }} style={{ ...selectStyle, cursor: "pointer", fontWeight: 700 }}>
            Search
          </button>
        </div>

        {!browseClientId ? (
          <div style={{ padding: "20px", textAlign: "center", color: "var(--text-dim)", fontSize: "13px" }}>
            Pick a client above (or click "View results" on a job) to browse its records.
          </div>
        ) : (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <span style={{ fontSize: "12px", color: "var(--text-dim)" }}>
                {browseLoading ? "Loading…" : `${profilesTotal} record(s)`}
                {selected.size > 0 && ` · ${selected.size} selected`}
              </span>
              <button
                onClick={deleteSelected}
                disabled={selected.size === 0 || deleting}
                style={{
                  padding: "6px 14px", borderRadius: "8px",
                  background: selected.size ? "rgba(233,80,83,0.15)" : "var(--bg-surface-3, #1d2939)",
                  border: `1px solid ${selected.size ? "rgba(233,80,83,0.4)" : "var(--border-subtle, rgba(255,255,255,0.1))"}`,
                  color: selected.size ? "var(--danger, #e95053)" : "var(--text-dim)",
                  fontSize: "12px", fontWeight: 700, cursor: selected.size ? "pointer" : "not-allowed",
                }}
              >
                🗑 Delete selected {selected.size > 0 ? `(${selected.size})` : ""}
              </button>
            </div>

            <div style={{ overflowX: "auto" }}>
              <table className="core_table">
                <thead>
                  <tr>
                    <th><input type="checkbox" checked={profiles.length > 0 && selected.size === profiles.length} onChange={toggleAll} /></th>
                    <th>Platform</th>
                    <th>Name</th>
                    <th>URL</th>
                    <th>Status</th>
                    <th>Risk</th>
                    <th>Phase</th>
                    <th>Last seen</th>
                  </tr>
                </thead>
                <tbody>
                  {profiles.length === 0 ? (
                    <tr><td colSpan={8} style={{ textAlign: "center", padding: "20px", color: "var(--text-dim)" }}>
                      {browseLoading ? "Loading…" : "No records match these filters."}
                    </td></tr>
                  ) : (
                    profiles.map((p) => (
                      <tr key={p.id}>
                        <td><input type="checkbox" checked={selected.has(p.id)} onChange={() => toggleOne(p.id)} /></td>
                        <td style={{ textTransform: "capitalize" }}>{p.platform}</td>
                        <td style={{ maxWidth: "180px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {p.profile_name || "—"}
                        </td>
                        <td style={{ maxWidth: "220px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          <a href={p.url} target="_blank" rel="noreferrer" style={{ color: "var(--accent, #7c5cff)" }}>{p.url}</a>
                        </td>
                        <td><Badge color={STATUS_BADGE[p.status] ?? "var(--text-dim)"}>{p.status}</Badge></td>
                        <td>{p.risk_score ?? "—"}</td>
                        <td style={{ textTransform: "capitalize" }}>{p.phase || "—"}</td>
                        <td title={exactTime(p.analysed_at)}>{relativeTime(p.analysed_at) !== "—" ? relativeTime(p.analysed_at) : "—"}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <div style={{ display: "flex", justifyContent: "center", gap: "12px", alignItems: "center", marginTop: "12px" }}>
              <button
                onClick={() => setOffset(Math.max(0, offset - BROWSE_PAGE_SIZE))}
                disabled={offset === 0}
                style={{ ...selectStyle, cursor: offset === 0 ? "not-allowed" : "pointer" }}
              >
                ← Prev
              </button>
              <span style={{ fontSize: "12px", color: "var(--text-dim)" }}>
                {offset + 1}–{Math.min(offset + BROWSE_PAGE_SIZE, profilesTotal)} of {profilesTotal}
              </span>
              <button
                onClick={() => setOffset(offset + BROWSE_PAGE_SIZE)}
                disabled={offset + BROWSE_PAGE_SIZE >= profilesTotal}
                style={{ ...selectStyle, cursor: offset + BROWSE_PAGE_SIZE >= profilesTotal ? "not-allowed" : "pointer" }}
              >
                Next →
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  background: "var(--bg-inner, #0b1220)", border: "1px solid var(--border-color, #344054)",
  borderRadius: "8px", padding: "8px 10px", color: "var(--text-main)", fontSize: "12px", outline: "none",
};
