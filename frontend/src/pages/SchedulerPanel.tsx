// Live view of the always-on round-robin engine (backend/services/
// round_robin_service.py), every client it cycles through, when it last
// ran, whether that run succeeded, and a rough ETA until it comes back
// around. Refreshed on an interval, not the 2s job-polling cadence used
// for a single in-flight job, this is a status table over ~hundreds of
// clients, not a live progress bar.
import { Fragment, useCallback, useEffect, useState } from "react";
import { toast } from "react-hot-toast";
import { jobsApi } from "../api/jobsApi";
import { clientsApi } from "../api/clientsApi";
import type { JobEvent } from "../api/types";
import {
  schedulerApi,
  type SchedulerQueue,
  type SchedulerStatus,
} from "../api/schedulerApi";
import { PlayIcon, PauseIcon, AlertTriangleIcon, SearchIcon, StopIcon } from "../components/AppIcons";

const REFRESH_MS = 20_000;
const EVENTS_REFRESH_MS = 3_000;
// The queue is served from memory (no Mongo read), so it can be polled at
// roughly the cadence a single job is, instead of the 20s status refresh.
const QUEUE_REFRESH_MS = 3_000;

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

const QUEUE_BTN: React.CSSProperties = {
  padding: "3px 8px",
  borderRadius: "6px",
  border: "1px solid var(--border-color)",
  background: "var(--bg-inner)",
  color: "var(--text-muted)",
  fontSize: "11px",
  fontWeight: 600,
  cursor: "pointer",
  lineHeight: 1.6,
};

function SourceTag({ source }: { source: "scheduler" | "manual" | "queue" | "rotation" }) {
  const look = {
    scheduler: { label: "scheduler", color: "var(--accent, #7c5cff)" },
    manual: { label: "manual", color: "var(--cyan-bright, #00e5ff)" },
    queue: { label: "queued by you", color: "var(--warn-yellow, #fdb71b)" },
    rotation: { label: "rotation", color: "var(--text-dim)" },
  }[source];
  return (
    <span
      style={{
        fontSize: "10px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.5px",
        color: look.color, border: `1px solid ${look.color}`, borderRadius: "999px",
        padding: "1px 7px", whiteSpace: "nowrap",
      }}
    >
      {look.label}
    </span>
  );
}

// The live queue: what is running or waiting right now (real jobs, which
// can be stopped) and which clients come next (not started yet, so they
// can be reordered or dropped instead). Deliberately two lists rather than
// one -- the actions available on each half are different, and merging
// them would put a Stop button next to something that has not started.
function QueuePanel({
  onChanged,
  clientNames,
}: {
  onChanged: () => void;
  clientNames: Record<string, string>;
}) {
  const [queue, setQueue] = useState<SchedulerQueue | null>(null);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [addId, setAddId] = useState("");

  const load = useCallback(async () => {
    try {
      setQueue(await schedulerApi.queue(12));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, QUEUE_REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  // Every mutation refreshes from the server rather than patching local
  // state: the engine moves on its own between renders, so a locally
  // spliced list would drift from what it is actually about to run.
  const act = async (id: string, fn: () => Promise<unknown>, done: string) => {
    setBusyId(id);
    try {
      await fn();
      toast.success(done);
      await load();
      onChanged();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusyId("");
    }
  };

  const jobs = queue?.jobs ?? [];
  const upcoming = queue?.upcoming ?? [];
  const queuedIds = new Set(queue?.queue ?? []);
  const addable = Object.entries(clientNames).filter(([id]) => !queuedIds.has(id));

  return (
    <div
      style={{
        marginBottom: "20px", background: "var(--bg-surface)",
        border: "1px solid var(--border-subtle)", borderRadius: "12px", padding: "14px 16px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "10px", flexWrap: "wrap", marginBottom: "10px" }}>
        <div style={{ fontSize: "10px", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "1px", fontWeight: 700 }}>
          Queue &mdash; {jobs.length} in flight, {upcoming.length} up next
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <select
            value={addId}
            onChange={(e) => setAddId(e.target.value)}
            style={{
              background: "var(--bg-inner)", border: "1px solid var(--border-color)", borderRadius: "6px",
              color: "var(--text-main)", fontSize: "12px", padding: "4px 8px", maxWidth: "200px",
            }}
          >
            <option value="">Queue a client&hellip;</option>
            {addable.map(([id, name]) => (
              <option key={id} value={id}>{name}</option>
            ))}
          </select>
          <button
            style={{ ...QUEUE_BTN, opacity: addId ? 1 : 0.5, cursor: addId ? "pointer" : "not-allowed" }}
            disabled={!addId || busyId === addId}
            onClick={() => {
              const id = addId;
              setAddId("");
              void act(id, () => schedulerApi.enqueue(id, true), `${clientNames[id] ?? id} will run next`);
            }}
            title="Put this client at the front of the queue"
          >
            &uarr; Run next
          </button>
        </div>
      </div>

      {error && <div style={{ fontSize: "12px", color: "var(--danger)", marginBottom: "8px" }}>{error}</div>}

      {jobs.length === 0 && upcoming.length === 0 && (
        <div style={{ fontSize: "13px", color: "var(--text-dim)", padding: "6px 0" }}>
          Nothing running and nothing queued.
          {queue && !queue.running && " The engine is paused."}
        </div>
      )}

      {jobs.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginBottom: upcoming.length ? "14px" : 0 }}>
          {jobs.map((j) => (
            <div
              key={j.job_id}
              style={{
                display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap",
                padding: "8px 10px", borderRadius: "8px",
                background: j.status === "running" ? "rgba(124, 92, 255, 0.07)" : "var(--bg-inner)",
                border: `1px solid ${j.status === "running" ? "var(--accent, #7c5cff)" : "var(--border-subtle)"}`,
              }}
            >
              <span
                style={{
                  fontSize: "11px", fontWeight: 700, minWidth: "62px",
                  color: j.status === "running" ? "var(--accent, #7c5cff)" : "var(--warn-yellow, #fdb71b)",
                }}
              >
                {j.status === "running" ? "● running" : "◌ waiting"}
              </span>
              <strong style={{ fontSize: "13px", color: "var(--text-primary, #fff)" }}>{j.client_name}</strong>
              <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                {j.kind}{j.platform ? ` · ${j.platform}` : " · all platforms"}
              </span>
              <SourceTag source={j.source} />
              {j.blocked_by && (
                <span style={{ fontSize: "11px", color: "var(--text-dim)" }}>
                  waiting on {j.blocked_by.kind} for{" "}
                  {clientNames[j.blocked_by.client_id] ?? j.blocked_by.client_id}
                </span>
              )}
              <span style={{ flex: 1 }} />
              <button
                style={{
                  ...QUEUE_BTN,
                  color: "#ff6b6b",
                  borderColor: "rgba(239,68,68,0.5)",
                  background: "rgba(239,68,68,0.12)",
                  cursor: busyId === j.job_id ? "progress" : "pointer",
                  opacity: busyId === j.job_id ? 0.6 : 1,
                  display: "inline-flex", alignItems: "center", gap: "4px",
                }}
                disabled={busyId === j.job_id}
                onClick={() =>
                  void act(
                    j.job_id,
                    () => jobsApi.cancelJob(j.job_id),
                    `Stopped ${j.kind} for ${j.client_name}`,
                  )
                }
                title={j.status === "running" ? "Abort this run" : "Drop this waiting run"}
              >
                <StopIcon size={10} color="#ff6b6b" />
                {busyId === j.job_id ? "Stopping…" : j.status === "running" ? "Stop" : "Remove"}
              </button>
            </div>
          ))}
        </div>
      )}

      {upcoming.length > 0 && (
        <>
          <div style={{ fontSize: "10px", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "1px", fontWeight: 700, marginBottom: "6px" }}>
            Up next
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            {upcoming.map((u, i) => {
              const mine = u.source === "queue";
              const busy = busyId === u.client_id;
              return (
                <div
                  key={`${u.client_id}-${i}`}
                  style={{
                    display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap",
                    padding: "6px 10px", borderRadius: "8px", background: "var(--bg-inner)",
                    border: `1px solid ${mine ? "rgba(253,183,27,0.35)" : "var(--border-subtle)"}`,
                    opacity: busy ? 0.6 : 1,
                  }}
                >
                  <span style={{ fontSize: "11px", color: "var(--text-dim)", minWidth: "20px", fontFamily: "var(--font-mono)" }}>
                    {i + 1}.
                  </span>
                  <strong style={{ fontSize: "13px", color: "var(--text-main)" }}>{u.client_name}</strong>
                  <SourceTag source={u.source} />
                  <span style={{ flex: 1 }} />
                  {mine ? (
                    <>
                      <button
                        style={QUEUE_BTN}
                        disabled={busy || i === 0}
                        onClick={() => void act(u.client_id, () => schedulerApi.moveInQueue(u.client_id, "up"), "Moved up")}
                        title="Move up the queue"
                      >
                        &uarr;
                      </button>
                      <button
                        style={QUEUE_BTN}
                        disabled={busy}
                        onClick={() => void act(u.client_id, () => schedulerApi.moveInQueue(u.client_id, "down"), "Moved down")}
                        title="Move down the queue"
                      >
                        &darr;
                      </button>
                      <button
                        style={{ ...QUEUE_BTN, color: "#ff6b6b", borderColor: "rgba(239,68,68,0.4)" }}
                        disabled={busy}
                        onClick={() =>
                          void act(
                            u.client_id,
                            () => schedulerApi.dequeue(u.client_id),
                            `${u.client_name} removed from the queue`,
                          )
                        }
                        title="Remove from the queue (it stays in the normal rotation)"
                      >
                        &#10005;
                      </button>
                    </>
                  ) : (
                    <button
                      style={QUEUE_BTN}
                      disabled={busy}
                      onClick={() =>
                        void act(
                          u.client_id,
                          () => schedulerApi.enqueue(u.client_id, true),
                          `${u.client_name} will run next`,
                        )
                      }
                      title="Jump this client to the front of the queue"
                    >
                      &uarr; Run next
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

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
  const [expanded, setExpanded] = useState<string>("");
  const [busyClient, setBusyClient] = useState("");

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

  const now = useNowTick();

  const clients = (status?.clients ?? []).filter((c) =>
    !filter.trim() || c.name.toLowerCase().includes(filter.trim().toLowerCase()),
  );
  const runningNow = clients.filter((c) => c.current_phase);

  // Unfiltered on purpose: the queue names clients by id, including ones
  // the search box is currently hiding, and a blocked-by row can point at
  // a client that is not in this table at all.
  const clientNames = Object.fromEntries(
    (status?.clients ?? []).map((c) => [c.client_id, c.name]),
  );

  // Moves a client up/down in the FULL (unfiltered) list and persists it,
  // so the round-robin engine's own rotation picks up the new order on its
  // very next lap (see client_repository.list_all's sort). Deliberately
  // computed off `status.clients`, not the filtered `clients` view above --
  // reordering within a filtered subset would silently scramble the
  // relative position of every row the search box is currently hiding.
  const [reordering, setReordering] = useState(false);
  const moveClient = async (clientId: string, direction: "up" | "down") => {
    if (!status) return;
    const ids = status.clients.map((c) => c.client_id);
    const i = ids.indexOf(clientId);
    const j = direction === "up" ? i - 1 : i + 1;
    if (i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    setReordering(true);
    try {
      await clientsApi.reorderClients(ids);
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setReordering(false);
    }
  };

  const toggleClient = async (clientId: string, enabled: boolean) => {
    setBusyClient(clientId);
    try {
      await schedulerApi.setClientEnabled(clientId, enabled);
      toast.success(
        enabled
          ? `${clientNames[clientId] ?? clientId} is back in the rotation`
          : `${clientNames[clientId] ?? clientId} parked -- the engine will skip it`,
      );
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusyClient("");
    }
  };

  return (
    <div style={{ padding: "24px", color: "var(--text-main, #f2f4f7)", maxWidth: "1100px", margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "20px", flexWrap: "wrap", gap: "12px" }}>
        <div>
          <h1 style={{ fontSize: "22px", fontWeight: 700, color: "var(--text-primary, #fff)", margin: 0, letterSpacing: "-0.3px" }}>
            🔁 Scheduler
          </h1>
          <p style={{ fontSize: "13px", color: "var(--text-muted, #98a2b3)", margin: "4px 0 0 0" }}>
            Runs discovery for one client, then the next, in the order below -- at most twice a day
            per client. It never starts itself: click Resume to begin, and it keeps going through
            the list until you click Pause. Use the ▲▼ arrows on a row to change the order.
          </p>
        </div>
        {status && (
          <div style={{ display: "flex", alignItems: "center", gap: "14px", flexWrap: "wrap" }}>
            <button
              onClick={toggleEngine}
              disabled={toggling}
              style={{
                padding: "10px 18px", borderRadius: "10px", cursor: toggling ? "wait" : "pointer",
                background: status.running ? "rgba(233,80,83,0.12)" : "rgba(54,181,160,0.12)",
                border: `1px solid ${status.running ? "rgba(233,80,83,0.4)" : "rgba(54,181,160,0.4)"}`,
                color: status.running ? "var(--danger)" : "var(--success)",
                fontSize: "13px", fontWeight: 700, whiteSpace: "nowrap",
                display: "inline-flex", alignItems: "center", gap: "6px",
              }}
            >
              {status.running ? (
                <>
                  <PauseIcon size={14} /> Pause engine
                </>
              ) : (
                <>
                  <PlayIcon size={14} /> Resume engine
                </>
              )}
            </button>
          </div>
        )}
      </div>

      {error && (
        <div style={{
          padding: "10px 16px", background: "rgba(233, 80, 83,0.1)", border: "1px solid rgba(233, 80, 83,0.25)",
          color: "var(--danger)", borderRadius: "10px", marginBottom: "16px", fontSize: "13px",
          display: "flex", alignItems: "center", gap: "8px",
        }}>
          <AlertTriangleIcon size={15} color="var(--danger)" />
          <span>{error}</span>
        </div>
      )}

      {status && status.consecutive_failures >= 3 && (
        <div style={{
          padding: "10px 16px", background: "rgba(255,193,7,0.1)", border: "1px solid rgba(255,193,7,0.3)",
          color: "var(--warn-yellow, #fdb71b)", borderRadius: "10px", marginBottom: "16px", fontSize: "13px",
          display: "flex", alignItems: "flex-start", gap: "8px",
        }}>
          <AlertTriangleIcon size={16} color="var(--warn-yellow, #fdb71b)" style={{ marginTop: 2 }} />
          <span>
            {status.consecutive_failures} client turns in a row have failed. The engine is backing off
            automatically between attempts. This usually means something systemic (not one client's own
            keywords/session) -- check the Incidents list, or an admin email if Mail alerts are configured.
          </span>
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

      {status && <QueuePanel onChanged={load} clientNames={clientNames} />}

      <div style={{ position: "relative", width: "100%", marginBottom: "12px", display: "flex", alignItems: "center" }}>
        <SearchIcon size={14} color="var(--text-muted, #98a2b3)" style={{ position: "absolute", left: "12px", pointerEvents: "none" }} />
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by client name…"
          style={{
            width: "100%", background: "var(--bg-inner)", border: "1px solid var(--border-color)",
            borderRadius: "10px", padding: "10px 12px 10px 34px", color: "var(--text-main)", fontSize: "13px", outline: "none",
            boxSizing: "border-box",
          }}
        />
      </div>

      <div style={{ overflowX: "auto" }}>
        <table className="core_table">
          <thead>
            <tr>
              <th title="Rotation order -- what the engine processes this client after/before">Order</th>
              <th>Client</th>
              <th>Status</th>
              <th>Last run</th>
              <th>Duration</th>
              <th>Runs</th>
              <th>Next run</th>
              <th>Note</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {!status ? (
              <tr><td colSpan={9} style={{ textAlign: "center", padding: "24px", color: "var(--text-dim)" }}>Loading…</td></tr>
            ) : clients.length === 0 ? (
              <tr><td colSpan={9} style={{ textAlign: "center", padding: "24px", color: "var(--text-dim)" }}>No clients with keywords set yet.</td></tr>
            ) : (
              clients.map((c) => {
                const look = c.last_run_status ? STATUS_LOOK[c.last_run_status] : null;
                const isOpen = expanded === c.client_id;
                const running = !!c.current_phase;
                const fullIds = status.clients.map((x) => x.client_id);
                const fullIndex = fullIds.indexOf(c.client_id);
                const filtered = !!filter.trim();
                return (
                  <Fragment key={c.client_id}>
                    <tr
                      onClick={() => setExpanded(isOpen ? "" : c.client_id)}
                      style={{
                        cursor: "pointer",
                        background: running
                          ? "rgba(124, 92, 255, 0.06)"
                          : c.queue_position !== null
                          ? "rgba(253, 183, 27, 0.06)"
                          : undefined,
                        opacity: c.scheduler_enabled ? 1 : 0.55,
                      }}
                      title="Click to see this client's live progress"
                    >
                      <td onClick={(e) => e.stopPropagation()} style={{ whiteSpace: "nowrap" }}>
                        <button
                          style={{ ...QUEUE_BTN, padding: "2px 6px" }}
                          disabled={reordering || filtered || fullIndex <= 0}
                          onClick={() => void moveClient(c.client_id, "up")}
                          title={filtered ? "Clear the filter to reorder" : "Move up in the rotation order"}
                        >
                          ▲
                        </button>
                        <button
                          style={{ ...QUEUE_BTN, padding: "2px 6px", marginLeft: "4px" }}
                          disabled={reordering || filtered || fullIndex < 0 || fullIndex >= fullIds.length - 1}
                          onClick={() => void moveClient(c.client_id, "down")}
                          title={filtered ? "Clear the filter to reorder" : "Move down in the rotation order"}
                        >
                          ▼
                        </button>
                      </td>
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
                        ) : !c.scheduler_enabled ? (
                          <span style={{ color: "var(--text-dim)", fontWeight: 600 }} title="Parked by an admin -- the engine skips this client entirely">
                            ⏸ skipped by admin
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
                      <td>
                        {running
                          ? "—"
                          : !c.scheduler_enabled
                          ? "skipped"
                          : c.queue_position !== null
                          ? `queued #${c.queue_position + 1}`
                          : status.running
                          ? etaLabel(c.eta_seconds)
                          : "paused"}
                      </td>
                      <td style={{ color: "var(--text-dim)", fontSize: "12px", maxWidth: "260px" }}>{c.last_run_note || "—"}</td>
                      {/* stopPropagation throughout: the row itself toggles
                          the event log, and an admin clicking Run next has
                          no reason to also expand a log panel. */}
                      <td onClick={(e) => e.stopPropagation()} style={{ whiteSpace: "nowrap" }}>
                        <button
                          style={{ ...QUEUE_BTN, marginRight: "6px" }}
                          disabled={busyClient === c.client_id || !c.scheduler_enabled || c.queue_position === 0}
                          onClick={() =>
                            void schedulerApi
                              .enqueue(c.client_id, true)
                              .then(() => {
                                toast.success(`${c.name} will run next`);
                                load();
                              })
                              .catch((err) => toast.error((err as Error).message))
                          }
                          title={
                            c.scheduler_enabled
                              ? "Jump this client to the front of the queue"
                              : "Paused clients cannot be queued -- re-enable it first"
                          }
                        >
                          &uarr; Run next
                        </button>
                        <button
                          style={{
                            ...QUEUE_BTN,
                            color: c.scheduler_enabled ? "var(--text-muted)" : "var(--success, #36b5a0)",
                            cursor: busyClient === c.client_id ? "progress" : "pointer",
                          }}
                          disabled={busyClient === c.client_id}
                          onClick={() => void toggleClient(c.client_id, !c.scheduler_enabled)}
                          title={
                            c.scheduler_enabled
                              ? "Take this client out of the rotation (manual runs still work)"
                              : "Put this client back in the rotation"
                          }
                        >
                          {c.scheduler_enabled ? "Skip" : "Un-skip"}
                        </button>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={9} style={{ padding: "0 0 12px" }}>
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
