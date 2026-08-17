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
};
