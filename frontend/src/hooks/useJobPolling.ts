import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { jobsApi } from "../api/jobsApi";
import type { Job, JobEvent, PlatformProgress } from "../api/types";

const TERMINAL = new Set(["done", "failed", "cancelled"]);
const POLL_MS = 2000;

// A discovery/analysis sweep can run 20-40 minutes -- previously the only
// way to know it finished was to keep the tab open and watch the progress
// bar, or periodically tab back in to check. `watch()` is always called
// right after the analyst clicks Search/Analyse, which is exactly the kind
// of user gesture browsers require before `Notification.requestPermission`
// will actually prompt (calling it unprompted on page load would just get
// silently ignored or auto-denied).
function ensureNotificationPermission(): void {
  if (typeof Notification === "undefined" || Notification.permission !== "default") return;
  Notification.requestPermission().catch(() => {});
}

function notifyJobFinished(job: Job): void {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  // An analyst actively watching the live progress bar doesn't need a
  // popup duplicating what's already on screen -- only notify if they've
  // tabbed away, which is the actual pain point ("no idea when results are
  // ready" after switching tabs).
  if (typeof document !== "undefined" && document.visibilityState === "visible" && document.hasFocus()) return;
  const kind = job.kind === "discovery" ? "Discovery sweep" : "Analysis run";
  const outcome = job.status === "done" ? "finished" : job.status === "failed" ? "failed" : "was cancelled";
  try {
    new Notification(`${kind} ${outcome}`, {
      body: `${job.platform ?? "All platforms"} · ${job.found} found${job.new_profiles ? ` (${job.new_profiles} new)` : ""}`,
      tag: job.id,
    });
  } catch {
    // Notification can throw in contexts that don't actually support it
    // despite the permission grant (e.g. some in-app webviews) -- non-fatal
  }
}

// Replaces the old WebSocket-driven useMultiJob: this backend dropped
// WebSocket progress in favor of polling + webhook (see
// backend/docs/adr/0002-polling-plus-webhook-over-websocket.md), so a
// watched job is polled on an interval instead of pushed to.
export function useJobPolling(onFinish?: () => void, onItem?: () => void) {
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [logs, setLogs] = useState<Record<string, JobEvent[]>>({});
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const seqs = useRef<Record<string, number>>({});
  const finish = useRef(onFinish);
  finish.current = onFinish;
  const item = useRef(onItem);
  item.current = onItem;

  // `clearTimeout` only cancels a *scheduled* poll -- it can't stop one
  // already in flight (mid-`await`). Each job's poll loop is tagged with an
  // epoch that's bumped whenever the loop is (re)started for that job id;
  // a loop whose epoch no longer matches when its await resolves knows it's
  // stale -- superseded by a newer watch() call, or torn down on unmount --
  // and neither applies its result nor reschedules itself. Without this, an
  // in-flight poll from a torn-down/superseded loop would setState after
  // unmount and/or re-arm a second concurrent timer for the same job id,
  // double-applying the same event batch into the log.
  const epochs = useRef<Record<string, number>>({});
  const mounted = useRef(true);

  const stopAll = useCallback(() => {
    mounted.current = false;
    Object.values(timers.current).forEach(clearTimeout);
    timers.current = {};
  }, []);

  const watch = useCallback((newJobs: Job[]) => {
    ensureNotificationPermission();
    setJobs((prev) => {
      const next = { ...prev };
      newJobs.forEach((j) => (next[j.id] = j));
      return next;
    });
    setLogs((prev) => {
      const next = { ...prev };
      newJobs.forEach((j) => (next[j.id] = next[j.id] || []));
      return next;
    });

    newJobs.forEach((j) => {
      if (timers.current[j.id]) clearTimeout(timers.current[j.id]);
      seqs.current[j.id] = j.last_seq || 0;
      const myEpoch = (epochs.current[j.id] = (epochs.current[j.id] || 0) + 1);
      const stale = () => !mounted.current || epochs.current[j.id] !== myEpoch;

      const poll = async () => {
        try {
          const [updated, events] = await Promise.all([
            jobsApi.job(j.id),
            jobsApi.jobEvents(j.id, seqs.current[j.id] || 0),
          ]);
          if (stale()) return;
          setJobs((prev) => ({ ...prev, [j.id]: updated }));

          if (events.items.length) {
            seqs.current[j.id] = events.last_seq;
            setLogs((prev) => ({
              ...prev,
              [j.id]: [...(prev[j.id] || []), ...events.items].slice(-200),
            }));
            if (events.items.some((e) => e.type === "item")) item.current?.();
          }

          if (TERMINAL.has(updated.status)) {
            delete timers.current[j.id];
            notifyJobFinished(updated);
            finish.current?.();
            return;
          }
        } catch {
          // transient fetch failure -- keep polling rather than giving up
        }
        if (stale()) return;
        timers.current[j.id] = setTimeout(poll, POLL_MS);
      };
      timers.current[j.id] = setTimeout(poll, POLL_MS);
    });
  }, []);

  useEffect(() => {
    mounted.current = true;
    return stopAll;
  }, [stopAll]);

  const activeJobs = Object.values(jobs).filter((j) => !TERMINAL.has(j.status));
  const running = activeJobs.length > 0;
  const aggregatedLog = Object.values(logs).flat();
  const message = activeJobs
    .map((j) => `${j.platform ?? "all"}: ${j.message || "processing..."}`)
    .join(" | ");

  // Merged per-platform progress across every currently-watched job of this
  // kind (in practice there's usually exactly one at a time -- discovery/
  // analysis both always run as a single job covering every ready
  // platform -- but merging keeps this correct if that ever changes).
  // A platform whose job later fails without an explicit "failed" progress
  // update (see analysis_service.py -- a raised exception skips straight to
  // the job's own FAILED status) is treated as failed here too, rather than
  // shown stuck at "running" forever.
  const platformProgress = useMemo(() => {
    const merged: Record<string, PlatformProgress> = {};
    for (const job of Object.values(jobs)) {
      for (const [platform, progress] of Object.entries(job.platforms || {})) {
        const jobFailed = job.status === "failed" || job.status === "cancelled";
        merged[platform] =
          jobFailed && progress.status === "running" ? { ...progress, status: "failed" } : progress;
      }
    }
    return merged;
  }, [jobs]);

  const cancelAll = () => {
    activeJobs.forEach((j) => jobsApi.cancelJob(j.id).catch(() => {}));
  };

  return {
    jobs,
    log: aggregatedLog,
    logsByJob: logs,
    watch,
    running,
    message,
    platformProgress,
    cancelAll,
  };
}
