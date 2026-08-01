import { useCallback, useEffect, useRef, useState } from "react";
import { jobsApi } from "../api/jobsApi";
import type { Job, JobEvent } from "../api/types";

const TERMINAL = new Set(["done", "failed", "cancelled"]);
const POLL_MS = 2000;

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

  const stopAll = useCallback(() => {
    Object.values(timers.current).forEach(clearTimeout);
    timers.current = {};
  }, []);

  const watch = useCallback((newJobs: Job[]) => {
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

      const poll = async () => {
        try {
          const [updated, events] = await Promise.all([
            jobsApi.job(j.id),
            jobsApi.jobEvents(j.id, seqs.current[j.id] || 0),
          ]);
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
            finish.current?.();
            return;
          }
        } catch {
          // transient fetch failure -- keep polling rather than giving up
        }
        timers.current[j.id] = setTimeout(poll, POLL_MS);
      };
      timers.current[j.id] = setTimeout(poll, POLL_MS);
    });
  }, []);

  useEffect(() => stopAll, [stopAll]);

  const activeJobs = Object.values(jobs).filter((j) => !TERMINAL.has(j.status));
  const running = activeJobs.length > 0;
  const aggregatedLog = Object.values(logs).flat();
  const message = activeJobs
    .map((j) => `${j.platform ?? "all"}: ${j.message || "processing..."}`)
    .join(" | ");

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
    cancelAll,
  };
}
