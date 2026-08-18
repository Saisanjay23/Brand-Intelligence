// API calls for the round-robin engine's status/control resource
// (backend/api/scheduler_routes.py).
import { json, post, url } from "./httpClient";

export interface SchedulerSlot {
  client_id: string | null;
  phase: "idle" | "checking" | "discovery" | "analysis";
  since: string;
}

export interface SchedulerClientStatus {
  client_id: string;
  name: string;
  last_run_at: string | null;
  last_run_status: "success" | "failed" | "skipped" | null;
  last_run_note: string;
  // wall-clock seconds the most recent completed turn took (discovery +
  // any analysis catch-up), null until this client has completed one
  last_run_duration_s: number | null;
  // total completed turns (success + failed + skipped) since this client
  // was created, how many times it has been through the rotation
  run_count: number;
  eta_seconds: number | null;
  // set only while the round-robin engine is on THIS client's turn right
  // now; null otherwise. When set, this row is live, not historical.
  current_phase: "checking" | "discovery" | "analysis" | null;
  current_since: string | null;
  // false = parked by an admin; the round-robin engine skips this client
  // entirely. Manual Discover/Analyse runs are unaffected.
  scheduler_enabled: boolean;
  // 0-based place in the admin-controlled queue, null when the client is
  // only in the normal rotation
  queue_position: number | null;
}

// One live job the engine is running or waiting to run. `source` says who
// started it: both the engine and an analyst's manual run share the same
// per-platform locks, so a `queued` engine job may simply be waiting on a
// manual one, and vice versa.
export interface SchedulerQueueJob {
  job_id: string;
  client_id: string;
  client_name: string;
  kind: string;
  platform: string | null;
  status: string;
  message: string;
  started: string | null;
  source: "scheduler" | "manual";
  blocked_by: { job_id: string; client_id: string; kind: string; platform: string | null } | null;
}

// A client the engine has not started yet. Only `source: "queue"` entries
// are the admin's own -- those can be reordered or removed; "rotation"
// entries are just the remainder of the current lap.
export interface SchedulerUpcoming {
  client_id: string;
  client_name: string;
  source: "queue" | "rotation";
  position: number;
}

export interface SchedulerQueue {
  running: boolean;
  jobs: SchedulerQueueJob[];
  upcoming: SchedulerUpcoming[];
  queue: string[];
}

export interface SchedulerStatus {
  running: boolean;
  slots: number;
  avg_duration_seconds: number;
  rotation_size: number | null;
  current: SchedulerSlot[];
  clients: SchedulerClientStatus[];
  // how many client turns in a row have failed with no success between
  // them, the engine backs off automatically as this climbs (see
  // backend/services/round_robin_service.py). A handful is normal
  // transient noise; a large/growing number means something systemic is
  // wrong, not one client's own problem.
  consecutive_failures: number;
  // whether this process auto-starts the engine on its NEXT boot,
  // independent of `running` (the engine's state right now). Toggled via
  // PUT /scheduler/autostart; does not itself start/stop anything.
  autostart: boolean;
}

export const schedulerApi = {
  status: () => fetch(url("/scheduler/status")).then(json<SchedulerStatus>),
  start: () => post("/scheduler/start", {}).then(json<SchedulerStatus>),
  stop: () => post("/scheduler/stop", {}).then(json<SchedulerStatus>),
  setAutostart: (enabled: boolean) =>
    fetch(url("/scheduler/autostart"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }).then(json<{ autostart: boolean }>),

  // In-memory only, so it is cheap enough to poll on a much tighter
  // interval than status() (which reads every client out of Mongo).
  queue: (limit = 12) =>
    fetch(url(`/scheduler/queue?limit=${limit}`)).then(json<SchedulerQueue>),
  enqueue: (clientId: string, front = true) =>
    post("/scheduler/queue", { client_id: clientId, front }).then(json<{ queue: string[] }>),
  moveInQueue: (clientId: string, direction: "up" | "down") =>
    post(`/scheduler/queue/${encodeURIComponent(clientId)}/move`, { direction }).then(
      json<{ queue: string[] }>,
    ),
  dequeue: (clientId: string) =>
    fetch(url(`/scheduler/queue/${encodeURIComponent(clientId)}`), { method: "DELETE" }).then(
      json<{ queue: string[] }>,
    ),
  setClientEnabled: (clientId: string, enabled: boolean) =>
    fetch(url(`/scheduler/clients/${encodeURIComponent(clientId)}/enabled`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }).then(json<{ client_id: string; scheduler_enabled: boolean }>),
};
