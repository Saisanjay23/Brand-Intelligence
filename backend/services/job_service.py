"""Background jobs: run a phase, record progress, persist results.

Job state (the Job objects, per-platform locks) lives in memory in THIS
process, so run a single API server. The actual scraping (Playwright
automation, CPU-bound JSON/regex parsing) runs in a SEPARATE child process
per job, not on this process's event loop -- that parsing alone can hold
the event loop busy for ~30-40% of a CPU core continuously while scraping,
which would otherwise make a simple PATCH or results poll take seconds
instead of being near-instant.

A caller polls `GET /jobs/{id}` or `GET /jobs/{id}/events?after_seq=N` for
progress, numbered so a reconnect after a gap never misses anything; an
optional `callback_url` gets POSTed the final state once the job reaches a
terminal status, so a caller doesn't have to poll aggressively either.

A discovery job always sweeps every ready platform for a client -- there's
no per-platform discovery call. An analysis job's `platform` is either one
specific platform (the auto-trigger-on-approval path batches by the
approved profile's own platform) or None (the manual catch-up trigger,
which analyses every approved-but-unanalysed profile across every platform).

NOTE: this module's fully-qualified import path (`backend.services.job_service`)
is itself part of a runtime contract on Windows -- `multiprocessing`'s
"spawn" start method re-imports this exact module in the child process and
looks up `_child_entry` by that qualified name. Moving this file requires
that import path to resolve correctly in the child, which it does as long
as the file lives at `backend/services/job_service.py`.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import queue as _queue_module
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from backend.utils.logging import get_logger
from backend.utils.metrics import jobs_finished_total, jobs_started_total

log = get_logger("services.jobs")

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"
TERMINAL = {DONE, FAILED, CANCELLED}
DISCOVERY, ANALYSIS = "discovery", "analysis"

# a job stays in memory once terminal -- a long-running process running
# daily cron sweeps needs a cap or this grows forever. Well above what any
# realistic polling/listing use needs to see at once.
MAX_JOBS_IN_MEMORY = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Event:
    seq: int
    job_id: str
    type: str  # queued|running|progress|item|done|failed|cancelled
    message: str = ""
    found: int = 0
    total: int = 0
    ts: str = ""

    def to_dict(self) -> dict:
        return {"seq": self.seq, "job_id": self.job_id, "type": self.type,
                "message": self.message, "found": self.found, "total": self.total, "ts": self.ts}


@dataclass
class Job:
    id: str
    kind: str  # discovery | analysis
    client_id: str
    platform: Optional[str]  # None = every ready platform
    params: dict
    status: str = QUEUED
    message: str = ""
    found: int = 0
    total: int = 0
    new_profiles: int = 0
    error: str = ""
    started: str = ""
    finished: str = ""
    events: list[Event] = field(default_factory=list)
    task: Optional[Any] = None  # asyncio.Task
    process: Optional["multiprocessing.Process"] = None
    callback_url: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "client_id": self.client_id, "platform": self.platform,
            "params": self.params, "status": self.status, "message": self.message,
            "found": self.found, "total": self.total, "new_profiles": self.new_profiles,
            "error": self.error, "started": self.started, "finished": self.finished,
            "last_seq": self.events[-1].seq if self.events else 0,
        }


def _kill_process_tree(proc: "multiprocessing.Process") -> None:
    """Terminate a job's worker AND whatever it launched (Chrome).
    proc.terminate() alone only stops the Python child, orphaning any
    browser it launched -- Windows needs `taskkill /T` to kill the whole
    tree in one call; elsewhere terminate() is enough."""
    import sys

    if not proc.pid:
        return
    if sys.platform == "win32":
        import subprocess
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10)
            return
        except Exception:
            pass
    try:
        proc.terminate()
    except Exception:
        pass


# Set only inside a spawned child process -- when set, emit() forwards to
# this queue instead of touching job.events, because a child process has
# no access to the parent's Job object at all.
_ipc_queue: Optional["multiprocessing.Queue"] = None


class JobManager:
    """Process-wide singleton. One live browser session at a time per
    platform, by design (see `_lock_key`)."""

    _instance: Optional["JobManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._seq = 0
        self.locks: dict[Any, asyncio.Lock] = {}

    def _lock_key(self, job: Job) -> Any:
        from backend.platforms import registry

        # a discovery job (platform=None, sweeps everything) locks per
        # kind only -- it internally serializes its own per-platform work.
        # An analysis job scoped to one platform locks that platform (or,
        # for a key-authed platform with no session to collide on, locks
        # per (platform, kind) so discovery/analysis run side by side).
        if job.platform is None:
            return ("all-platforms", job.kind)
        return (job.platform, job.kind) if registry.get(job.platform).uses_api_key else job.platform

    async def emit(self, job: Job, type_: str, message: str = "", found: Optional[int] = None, total: Optional[int] = None) -> None:
        if _ipc_queue is not None:
            try:
                _ipc_queue.put_nowait({"type": type_, "message": message, "found": found, "total": total})
            except Exception:
                pass
            return
        self._seq += 1
        if found is not None:
            job.found = found
        if total is not None:
            job.total = total
        if message:
            job.message = message
        job.events.append(Event(seq=self._seq, job_id=job.id, type=type_, message=job.message,
                                 found=job.found, total=job.total, ts=_now_iso()))

    def create(self, kind: str, client_id: str, params: dict, *, platform: Optional[str] = None, callback_url: str = "") -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, client_id=client_id, platform=platform,
                   params=params, callback_url=callback_url)
        self.jobs[job.id] = job
        self._evict_old_jobs()
        job.task = asyncio.create_task(self._guard(job))
        return job

    def _evict_old_jobs(self) -> None:
        """Drop the oldest TERMINAL jobs once the in-memory table exceeds
        MAX_JOBS_IN_MEMORY. Never touches a queued/running job."""
        overflow = len(self.jobs) - MAX_JOBS_IN_MEMORY
        if overflow <= 0:
            return
        finished = sorted((j for j in self.jobs.values() if j.status in TERMINAL),
                           key=lambda j: j.finished or j.started or "")
        for job in finished[:overflow]:
            del self.jobs[job.id]

    async def _guard(self, job: Job) -> None:
        lock = self.locks.setdefault(self._lock_key(job), asyncio.Lock())
        async with lock:
            job.status = RUNNING
            job.started = _now_iso()
            await self.emit(job, RUNNING, "starting")
            jobs_started_total.labels(job.platform or "all", job.kind).inc()

            ipc_queue: multiprocessing.Queue = multiprocessing.Queue()
            proc = multiprocessing.Process(
                target=_child_entry, args=(job.id, job.kind, job.client_id, job.platform, job.params, ipc_queue),
                daemon=True,
            )
            job.process = proc
            proc.start()
            loop = asyncio.get_running_loop()

            def _get_next() -> dict:
                while True:
                    try:
                        return ipc_queue.get(timeout=1.0)
                    except _queue_module.Empty:
                        if not proc.is_alive():
                            try:
                                return ipc_queue.get(timeout=0.5)
                            except _queue_module.Empty:
                                return {"type": "__failed__", "error": "worker process exited unexpectedly", "error_type": "WorkerCrash"}
                        continue

            try:
                while True:
                    item = await loop.run_in_executor(None, _get_next)
                    t = item.get("type")
                    if t == "__done__":
                        job.status = DONE
                        await self.emit(job, DONE, item.get("message") or job.message or "finished")
                        jobs_finished_total.labels(job.platform or "all", job.kind, DONE).inc()
                        break
                    if t == "__failed__":
                        job.status = FAILED
                        job.error = item.get("error", "unknown error")
                        log.error(f"job {job.id} failed: {job.error}")
                        await self.emit(job, FAILED, job.error)
                        jobs_finished_total.labels(job.platform or "all", job.kind, FAILED).inc()
                        from backend.services import incident_service as incidents_engine
                        await incidents_engine.record(
                            job.platform or "all", job.kind, job.client_id, job.id,
                            item.get("error_type", "Error"), job.error,
                        )
                        break
                    await self.emit(job, t, item.get("message") or "", item.get("found"), item.get("total"))
            except asyncio.CancelledError:
                _kill_process_tree(proc)
                job.status = CANCELLED
                await self.emit(job, CANCELLED, "cancelled")
                jobs_finished_total.labels(job.platform or "all", job.kind, CANCELLED).inc()
                raise
            finally:
                job.finished = _now_iso()
                proc.join(timeout=5)
                if proc.is_alive():
                    _kill_process_tree(proc)
                if job.status in TERMINAL and job.callback_url:
                    from backend.services.webhook_service import dispatch
                    asyncio.create_task(dispatch(job))

    async def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or not job.task or job.status in TERMINAL:
            return False
        if job.process and job.process.is_alive():
            _kill_process_tree(job.process)
        job.task.cancel()
        return True

    async def cancel_all(self) -> None:
        for job in self.jobs.values():
            if job.task and job.status not in TERMINAL:
                if job.process and job.process.is_alive():
                    _kill_process_tree(job.process)
                job.task.cancel()

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def recent(self, limit: int = 25, client_id: str = "") -> list[Job]:
        jobs = self.jobs.values()
        if client_id:
            jobs = [j for j in jobs if j.client_id == client_id]
        return sorted(jobs, key=lambda j: j.started or "", reverse=True)[:limit]


def _child_entry(job_id: str, kind: str, client_id: str, platform: Optional[str], params: dict, ipc_queue: "multiprocessing.Queue") -> None:
    """The actual entry point of a job's worker process. Must stay a plain
    module-level function -- Windows' multiprocessing "spawn" start method
    re-imports this module in the child and looks the target up by its
    qualified name, so a bound method or closure here would fail to pickle.

    discovery/analysis imports are LOCAL: each imports JobManager from this
    module for `.emit()`, so a module-level import here would be circular.
    """
    global _ipc_queue
    _ipc_queue = ipc_queue
    job = Job(id=job_id, kind=kind, client_id=client_id, platform=platform, params=params)
    try:
        if kind == DISCOVERY:
            from backend.services.discovery_service import run_discovery
            runner = run_discovery
        else:
            from backend.services.analysis_service import run_analysis
            runner = run_analysis
        asyncio.run(runner(job))
        ipc_queue.put({"type": "__done__", "message": job.message or "finished"})
    except Exception as e:
        log.error(f"job {job_id} (worker): {type(e).__name__}: {e}")
        try:
            ipc_queue.put({"type": "__failed__", "error": f"{type(e).__name__}: {e}", "error_type": type(e).__name__})
        except Exception:
            pass


job_manager = JobManager()
