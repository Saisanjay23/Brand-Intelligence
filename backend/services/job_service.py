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
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from backend.shared.logging import get_logger
from backend.shared.metrics import jobs_finished_total, jobs_started_total

log = get_logger("services.jobs")

# Dedicated to the per-job IPC pump ONLY. Each running job parks one thread
# here for its whole duration, so this must not be the default executor that
# `asyncio.to_thread` callers (the avatar media-proxy) also draw from. Sized
# well above any plausible number of concurrent jobs -- these threads are
# blocked on a queue, not doing work, so they cost almost nothing.
_IPC_POOL = ThreadPoolExecutor(max_workers=64, thread_name_prefix="job-ipc")

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"
TERMINAL = {DONE, FAILED, CANCELLED}
DISCOVERY, ANALYSIS = "discovery", "analysis"

# a job stays in memory once terminal -- a long-running process running
# daily cron sweeps needs a cap or this grows forever. Well above what any
# realistic polling/listing use needs to see at once.
MAX_JOBS_IN_MEMORY = 2000

# Per-job event ring. A discovery sweep emits one "item" event per saved
# page of results, so a long multi-keyword run produces thousands -- times
# MAX_JOBS_IN_MEMORY jobs, that is real memory held forever for scrollback
# nobody reads. Callers poll with `after_seq`, and the frontend keeps only
# the last 200 anyway; older events falling off is invisible to a caller
# that is keeping up, and `last_seq` still tells one that isn't that it
# missed a range rather than silently renumbering.
MAX_EVENTS_PER_JOB = 500


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
    # which client this job (and therefore this event) belongs to -- lets a
    # caller filter the global event stream down to one client's live
    # progress (see the Scheduler admin tab) without cross-referencing the
    # job list separately.
    client_id: str = ""

    def to_dict(self) -> dict:
        return {"seq": self.seq, "job_id": self.job_id, "type": self.type,
                "message": self.message, "found": self.found, "total": self.total, "ts": self.ts,
                "client_id": self.client_id}


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
    # per-platform breakdown -- {platform_id: {status, processed, total,
    # started (epoch seconds), updated (epoch seconds)}}. status is
    # pending|running|done|failed, mirroring the job's own status values.
    # Populated via emit()'s platform=... kwargs; see discovery_service.py /
    # analysis_service.py for where each platform's entries get updated.
    platform_progress: dict[str, dict] = field(default_factory=dict)
    # Which pooled session this job is holding right now, and which
    # platform it belongs to -- set via emit()'s session_id= kwarg at the
    # moment discovery_service.py/analysis_service.py acquire one, cleared
    # implicitly the instant job.status leaves "running" (nothing has to
    # explicitly release it). This is what sessions/manager.py's
    # _session_in_use() checks to show "currently running" in the Sessions
    # panel -- see that function's docstring for why deriving it from the
    # job's own lifecycle is more robust than a separately-tracked flag.
    session_id: str = ""
    session_platform: str = ""

    def to_dict(self) -> dict:
        now = time.time()
        platforms_out: dict[str, dict] = {}
        for pid, p in self.platform_progress.items():
            eta_seconds = None
            if (
                p["status"] == "running" and p["started"] and p["processed"] > 0
                and p["total"] > p["processed"]
            ):
                rate = (now - p["started"]) / p["processed"]
                eta_seconds = round(rate * (p["total"] - p["processed"]))
            platforms_out[pid] = {**p, "eta_seconds": eta_seconds}
        return {
            "id": self.id, "kind": self.kind, "client_id": self.client_id, "platform": self.platform,
            "params": self.params, "status": self.status, "message": self.message,
            "found": self.found, "total": self.total, "new_profiles": self.new_profiles,
            "error": self.error, "started": self.started, "finished": self.finished,
            "last_seq": self.events[-1].seq if self.events else 0,
            # oldest event still retained (see MAX_EVENTS_PER_JOB) -- a
            # caller resuming from `after_seq` below this knows it missed a
            # range instead of silently receiving a truncated stream
            "first_seq": self.events[0].seq if self.events else 0,
            "platforms": platforms_out,
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

# In-flight webhook deliveries. asyncio only holds a weak reference to a
# task, so without somewhere to keep it a fire-and-forget callback can be
# collected before it ever sends.
_PENDING_CALLBACKS: set = set()


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

    def blocking_job(self, job: Job) -> Optional[Job]:
        """The currently RUNNING job holding a lock a QUEUED job needs, if
        any. `status=queued` alone doesn't tell an analyst why -- it looks
        identical whether a job hasn't started yet or is stuck behind
        someone else's sweep. Exposing what it's actually blocked on (see
        job_controller.py) turns "queued" into "waiting on client X's
        Facebook sweep"."""
        if job.status != QUEUED:
            return None
        wanted = set(self._lock_keys(job))
        for other in self.jobs.values():
            if other.id != job.id and other.status == RUNNING and wanted & set(self._lock_keys(other)):
                return other
        return None

    def _lock_keys(self, job: Job) -> list:
        """Every lock this job must hold, sorted.

        The lock has to name the RESOURCE being contended -- a platform's
        session pool -- not the shape of the job. The previous scheme keyed
        on (platform, kind), which meant these three never collided:

            discovery      platform=None      -> ("all-platforms","discovery")
            analysis       platform="facebook"-> "facebook"
            analysis       platform=None      -> ("all-platforms","analysis")

        ...so a Facebook sweep, an approve-triggered Facebook analysis, and
        the 20-minute catch-up could all drive the SAME cookie session
        through three Playwright contexts at once. Beyond the ban risk, the
        two analysis jobs each call `urls_for` at their own start and would
        then visit an identical URL list concurrently.

        A job scoped to one platform takes that platform's lock. A job with
        platform=None sweeps every ready platform, so it takes all of them.
        Sorting guarantees a consistent acquisition order, which is what
        makes deadlock between two multi-platform jobs impossible.

        A key-authed platform (YouTube: an API key, no browser session to
        collide over) keeps its per-kind split, so discovery and analysis
        there still run side by side.
        """
        from backend.platforms import registry

        def key_for(platform_id: str):
            plat = registry.PLATFORMS.get(platform_id)
            return (platform_id, job.kind) if (plat and plat.uses_api_key) else platform_id

        if job.platform is not None:
            return [key_for(job.platform)]
        return sorted(
            (key_for(pid) for pid, p in registry.PLATFORMS.items() if p.enabled),
            key=repr,
        )

    async def emit(
        self, job: Job, type_: str, message: str = "",
        found: Optional[int] = None, total: Optional[int] = None,
        *, platform: Optional[str] = None, platform_status: Optional[str] = None,
        platform_processed: Optional[int] = None, platform_total: Optional[int] = None,
        new_profiles: Optional[int] = None, session_id: Optional[str] = None,
    ) -> None:
        if _ipc_queue is not None:
            try:
                _ipc_queue.put_nowait({
                    "type": type_, "message": message, "found": found, "total": total,
                    "platform": platform, "platform_status": platform_status,
                    "platform_processed": platform_processed, "platform_total": platform_total,
                    # Without this the counter only ever incremented on the
                    # CHILD's copy of the Job -- the parent, which is what
                    # `GET /jobs/{id}` serializes, never saw it, so every
                    # job in the API reported new_profiles: 0 no matter how
                    # many profiles it actually discovered.
                    "new_profiles": new_profiles if new_profiles is not None else job.new_profiles,
                    # same cross-process gap, for "which session is this job
                    # using right now" -- see Job.session_id's own comment
                    "session_id": session_id if session_id is not None else job.session_id,
                })
            except Exception:
                pass
            return
        self._seq += 1
        if found is not None:
            job.found = found
        if total is not None:
            job.total = total
        if new_profiles is not None:
            job.new_profiles = new_profiles
        if session_id is not None:
            job.session_id = session_id
            job.session_platform = platform or job.session_platform
        if message:
            job.message = message
        if platform is not None:
            entry = job.platform_progress.setdefault(
                platform, {"status": "pending", "processed": 0, "total": 0, "started": None, "updated": None},
            )
            if platform_status is not None:
                entry["status"] = platform_status
                if platform_status == "running" and entry["started"] is None:
                    entry["started"] = time.time()
            if platform_processed is not None:
                entry["processed"] = platform_processed
            if platform_total is not None:
                entry["total"] = platform_total
            entry["updated"] = time.time()
        job.events.append(Event(seq=self._seq, job_id=job.id, type=type_, message=job.message,
                                 found=job.found, total=job.total, ts=_now_iso(), client_id=job.client_id))
        if len(job.events) > MAX_EVENTS_PER_JOB:
            del job.events[:-MAX_EVENTS_PER_JOB]

    def existing_queued(self, kind: str, client_id: str, platform: Optional[str], *, force: bool = False) -> Optional[Job]:
        """An already-QUEUED job that will cover at least as much work as
        this request needs, if any.

        A plain (force=False) analysis job re-reads the current
        approved-and-unanalysed set when it starts -- so a second plain
        request for the same (client, platform) is redundant with the
        first: whichever runs first analyses everything owed, and the rest
        would find nothing to do. That's what this coalescing exists for:
        approving a profile auto-queues analysis, and an analyst clicking
        Validate through 200 discovery cards used to queue 200 jobs, 199 of
        which existed only to spawn a process and log "nothing to
        analyse". (The bulk-patch endpoint already avoided this by hand;
        the per-card path did not, and that is the path the UI actually
        uses.)

        force=True is NOT interchangeable with a plain job -- it means
        "re-analyse everything approved, even what a previous run already
        scored," which is strictly more work. So:
          - an incoming force=True request is NEVER coalesced: reusing an
            already-queued plain job would silently turn an explicit
            "run analysis again" click into a no-op the moment that job
            finds nothing new to do, which is exactly the bug this
            parameter exists to fix.
          - an incoming plain request MAY coalesce into an already-queued
            force job, since a full re-analysis is a superset of "whatever
            is still owed" -- it will do at least as much as the plain
            request needed.

        Deliberately only matches QUEUED, never RUNNING: a job already in
        flight read its URL list at ITS start, so anything approved since
        then genuinely does need a follow-up run.
        """
        if kind != ANALYSIS or force:
            return None
        for job in self.jobs.values():
            if job.status == QUEUED and job.kind == ANALYSIS and job.client_id == client_id:
                # a platform=None job sweeps everything, so it subsumes any
                # platform-scoped request for the same client
                if job.platform is None or job.platform == platform:
                    return job
        return None

    def create(self, kind: str, client_id: str, params: dict, *, platform: Optional[str] = None, callback_url: str = "") -> Job:
        # A callback_url makes a job individually observable to its caller,
        # so never coalesce one away -- the caller is waiting for that exact
        # job id to report back.
        force = bool(params.get("force")) if kind == ANALYSIS else False
        if not callback_url:
            if existing := self.existing_queued(kind, client_id, platform, force=force):
                log.debug(f"coalescing {kind} for {client_id}/{platform} into queued job {existing.id}")
                return existing

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

    @asynccontextmanager
    async def _hold_locks(self, job: Job):
        """Acquire every lock this job needs, in a globally consistent order.

        Sorted acquisition is what makes this deadlock-free: two jobs that
        both want {facebook, instagram} can never each hold one and wait on
        the other, because both always take facebook first.
        """
        keys = self._lock_keys(job)
        acquired: list[asyncio.Lock] = []
        try:
            for key in keys:
                lock = self.locks.setdefault(key, asyncio.Lock())
                await lock.acquire()
                acquired.append(lock)
            yield
        finally:
            for lock in reversed(acquired):
                lock.release()

    async def _guard(self, job: Job) -> None:
        async with self._hold_locks(job):
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
                    # _IPC_POOL, never the default executor: _get_next blocks
                    # for the ENTIRE lifetime of the job, so on the shared
                    # pool each running job permanently consumes one of the
                    # default (min(32, cpu+4)) worker threads. Anything else
                    # using asyncio.to_thread -- notably the avatar
                    # media-proxy -- would then silently stop responding
                    # once enough jobs were in flight, with no error
                    # anywhere to explain it.
                    item = await loop.run_in_executor(_IPC_POOL, _get_next)
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
                    await self.emit(
                        job, t, item.get("message") or "", item.get("found"), item.get("total"),
                        platform=item.get("platform"), platform_status=item.get("platform_status"),
                        platform_processed=item.get("platform_processed"), platform_total=item.get("platform_total"),
                        new_profiles=item.get("new_profiles"), session_id=item.get("session_id"),
                    )
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
                    # keep a strong reference: a bare create_task is only
                    # weakly held by the loop and can be garbage-collected
                    # mid-flight, silently dropping the callback
                    task = asyncio.create_task(dispatch(job))
                    _PENDING_CALLBACKS.add(task)
                    task.add_done_callback(_PENDING_CALLBACKS.discard)

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
