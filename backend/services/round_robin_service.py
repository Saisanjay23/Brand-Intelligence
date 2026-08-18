"""The always-on engine: continuously cycles through every client that has
keywords set, running a DISCOVERY sweep for each (analysis is not this
engine's job -- see _process_client),
bounded to a handful of concurrent slots (`settings.round_robin_slots`) so
400 clients never all fire in the same instant. This is what replaced
per-client `cron` scheduling (see scheduler_service.py's module docstring).

A manual Sweep/Analyze click on the Home page can still land on a client
mid-rotation at any time, that's fine as-is, no coordination needed here:
`job_service.JobManager.existing_queued()` coalescing and its per-resource
locks already make a manual run and a round-robin turn for the same client
either merge into one job or queue safely behind each other, never both run
at once.

Each slot is a plain `while True` worker: pull the next client id off a
shared rotation cursor, pre-flight-check that at least one platform has a
usable session, run discovery, record the result, repeat. Each client is
swept at most MAX_RUNS_PER_DAY times a day (enforced as a minimum gap of
DISCOVERY_INTERVAL_HOURS between runs); the rotation skips anyone swept
more recently than that, so the engine idles instead of re-sweeping the
same handful of clients all day. Modelled directly on
`scheduler_service.catch_up_analysis`'s existing ready/skip/breaker pattern
-- see `backend.platforms.registry.ready_platforms()`, shared by both.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.config.settings import settings
from backend.shared.logging import get_logger

log = get_logger("services.round_robin")

_running = False
_tasks: list[asyncio.Task] = []

_rotation: list[str] = []
_cursor = 0
_rotation_lock = asyncio.Lock()

# Client ids an admin has explicitly pushed to the front, in the order they
# should be served. Drained before the automatic rotation, so "run this one
# next" beats "whatever the lap says comes next". Deliberately separate
# from `_rotation` rather than reordering it in place: `_rotation` is
# rebuilt from Mongo once per lap, so anything written into it is erased at
# the next lap boundary, silently and unpredictably from an admin's point
# of view. This list is the operator's, and only they add to or remove
# from it. In memory only -- a queue jump is a "do this now" instruction,
# not a standing preference; it should not outlive a restart.
_priority: list[str] = []

# How often the engine sweeps any one client. It used to cycle
# continuously -- with a handful of clients that meant re-sweeping the same
# ones every few minutes, which is far more traffic (and ban risk) than
# impersonation discovery needs.
#
# The rule is a CAP: no client is swept more than MAX_RUNS_PER_DAY times in
# a day. It is enforced as a minimum gap rather than a counter, so the runs
# also come out evenly spaced instead of two back-to-back followed by
# twenty-two idle hours -- a burst looks far more like automation to the
# platforms than a steady cadence does. Change MAX_RUNS_PER_DAY and the gap
# follows.
#
# An admin's explicit "run next" (the Scheduler tab's queue) deliberately
# IGNORES this: it is a direct instruction, not part of the rotation.
MAX_RUNS_PER_DAY = 2
DISCOVERY_INTERVAL_HOURS = 24 / MAX_RUNS_PER_DAY

# True while suspended for want of any usable session anywhere, exists
# purely so "everything is down" fires one incident on the transition
# rather than one per client for as long as the outage lasts (same pattern
# as scheduler_service._breaker_open).
_breaker_open = False

# How many client turns in a row have failed with no successes between them
#, across every slot, not per-client. A client-specific problem (its own
# keywords/session) doesn't climb this; something breaking the underlying
# job-worker spawn mechanism itself does, and that failure mode showed up
# in practice: dozens of job-worker processes created back to back with no
# pacing exhausted a Windows multiprocessing resource (pipe/semaphore
# handles), after which every subsequent spawn failed within ~1 second
# and with no backoff, the engine hammered that failure in a ~1.5s loop
# instead of giving the system a chance to recover. This counter drives an
# exponential backoff per slot on consecutive failure, and trips one
# incident (not one per failure) if it climbs past a real-problem threshold.
_consecutive_failures = 0
_failure_incident_open = False
MAX_BACKOFF_S = 120
FAILURE_INCIDENT_THRESHOLD = 5

# Per-platform "no usable session" notification debounce. Deleting a
# session (or every session in a pool going stale) does NOT go through
# sessions/manager.py::mark_session_failed at all; that only fires from a
# live scrape hitting a real error mid-job, which never happens for a
# platform with zero sessions (it's silently excluded from `ready` before a
# job would ever try it). And the "all platforms down" breaker above only
# fires when EVERY platform is unusable, so one platform quietly losing its
# only session while the others stay healthy was previously invisible:
# no incident, no email, ever. This is the fix: every pre-flight check
# below reports each individually-unavailable platform here, debounced per
# platform (not relying solely on alerting_service's own 1h email debounce,
# since without this every one of ~400 clients' turns would otherwise write
# a fresh incident DOCUMENT even while emails stayed suppressed).
_platform_unavailable_last_notified: dict[str, float] = {}
PLATFORM_NOTIFY_DEBOUNCE_S = 3600

_PLATFORM_STATE_MESSAGES = {
    "missing": "session missing or not configured -- no credential/cookie set for this platform at all",
    "incomplete": "session incomplete -- a required cookie is missing from what was exported",
    "exhausted": "every session in the pool is quarantined or rate-limited right now",
}

# Exponential moving average of seconds spent per client turn, across all
# slots, feeds the Scheduler tab's approximate "next run" ETA. Seeded at a
# plausible guess so the very first estimate isn't wildly off.
_avg_duration_s = 90.0

# What each slot is doing right now, keyed by slot index, surfaced on the
# Scheduler tab so an admin can see the engine is actually alive and what
# it's touching, not just a spinner.
_slot_state: dict[int, dict] = {}

# Job ids the engine itself started and is still waiting on. stop() needs
# this: cancelling a slot's worker task only unblocks the WORKER, it does
# nothing to the job -- so pausing the engine used to leave the job (and
# its Chrome process tree) running to completion while the Scheduler tab
# happily reported "stopped".
_engine_job_ids: set[str] = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def notify_unavailable_platforms(ready: list[str], unavailable: dict[str, str]) -> None:
    """One debounced incident (and, via incident_service, one debounced
    email) per platform that currently has no usable session, whether or
    not OTHER platforms are still fine. See the module-level comment above
    _platform_unavailable_last_notified for why this exists separately from
    the "every platform is down" breaker below.

    Public, and called from BOTH this engine's own per-client pre-flight
    check and scheduler_service.catch_up_analysis's independent 20-minute
    sweep; the debounce state above needs to be shared so the two don't
    each think they're first and double-notify, and catch-up's fixed
    interval is what still catches a platform going dark while this engine
    happens to be paused."""
    from backend.services import incident_service as incidents_engine

    now = time.time()
    for platform_id in ready:
        _platform_unavailable_last_notified.pop(platform_id, None)

    from backend.platforms import registry

    for platform_id, state in unavailable.items():
        # A platform that sweeps fine without credentials is not impaired by
        # having none. TikTok raised 31 of these in a single day while its
        # sweeps were running anonymously and returning results -- a
        # critical-severity email about a condition that costs one field.
        # See Platform.anonymous_context_path.
        plat = registry.PLATFORMS.get(platform_id)
        if plat is not None and plat.can_run_anonymously:
            continue
        last = _platform_unavailable_last_notified.get(platform_id, 0)
        if now - last < PLATFORM_NOTIFY_DEBOUNCE_S:
            continue
        _platform_unavailable_last_notified[platform_id] = now
        detail = _PLATFORM_STATE_MESSAGES.get(state, f"session state: {state}")
        log.warning(f"round-robin: {platform_id} has no usable session -- {detail}")
        await incidents_engine.record(
            platform_id, "discovery", "-- round robin --", "round-robin-platform-check",
            "SessionUnavailable",
            f"The automatic round-robin engine could not use {platform_id} while checking a client "
            f"just now -- {detail}. Update the session under Admin -> Sessions to resume scraping this "
            "platform; other platforms are unaffected.",
        )


def _due_for_discovery(client: dict) -> bool:
    """Has DISCOVERY_INTERVAL_HOURS passed since this client last ran?

    Reads `last_run_at`, which `record_run_result` stamps at the end of
    every turn. A client that has never run has no stamp and is due
    immediately. An unparseable/absent value is treated as due rather than
    silently parking the client forever -- the failure mode of the guard
    itself must be "sweeps too often", never "stops sweeping".
    """
    last = client.get("last_run_at")
    if not last:
        return True
    if isinstance(last, str):
        try:
            last = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except ValueError:
            return True
    if not isinstance(last, datetime):
        return True
    # Mongo hands back naive-but-UTC datetimes (see database/connection.py)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last) >= timedelta(hours=DISCOVERY_INTERVAL_HOURS)


async def _is_schedulable(client_id: str) -> bool:
    """Whether the engine should still take a turn on this client: it has
    to exist, have keywords, and not have been parked by an admin. Checked
    at the moment it is pulled off the priority list, not when it was put
    there -- a client can be deleted, emptied or paused in between."""
    from backend.database.repositories import client_repository as clients_db

    client = await clients_db.try_get(client_id)
    return bool(
        client
        and (client.get("name_keywords") or client.get("domain_keywords"))
        and client.get("scheduler_enabled", True)
    )


def priority_queue() -> list[str]:
    """The admin-controlled front of the queue, in service order."""
    return list(_priority)


def run_next(client_id: str) -> list[str]:
    """Put this client at the head of the queue. Idempotent: asking twice
    moves it to the front, it never queues the same client twice."""
    if client_id in _priority:
        _priority.remove(client_id)
    _priority.insert(0, client_id)
    log.info(f"round-robin: {client_id} moved to the front of the queue")
    return list(_priority)


def enqueue(client_id: str) -> list[str]:
    """Append to the admin queue, behind anything already waiting there."""
    if client_id not in _priority:
        _priority.append(client_id)
        log.info(f"round-robin: {client_id} added to the queue")
    return list(_priority)


def move(client_id: str, direction: str) -> list[str]:
    """Shuffle one client up or down within the admin queue. A no-op at
    either end rather than an error -- the UI shows the arrows on every
    row and clicking the one at the top should do nothing, not fail."""
    if client_id not in _priority:
        raise ValueError(f"{client_id!r} is not in the queue")
    i = _priority.index(client_id)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(_priority):
        _priority[i], _priority[j] = _priority[j], _priority[i]
    return list(_priority)


def dequeue(client_id: str) -> list[str]:
    """Drop a client from the admin queue. Does NOT stop a turn already in
    flight for it (that is a job cancel, see POST /jobs/{id}/cancel) and
    does not remove it from the automatic rotation (that is the per-client
    enable toggle)."""
    if client_id in _priority:
        _priority.remove(client_id)
        log.info(f"round-robin: {client_id} removed from the queue")
    return list(_priority)


def upcoming(limit: int = 12) -> list[dict]:
    """The next clients the engine will take, in order: the admin queue
    first, then the remainder of the current lap. `source` tells the two
    apart, since only the queued ones can be reordered or removed."""
    out = [{"client_id": cid, "source": "queue", "position": i} for i, cid in enumerate(_priority)]
    if _rotation:
        remaining = _rotation[_cursor:] + _rotation[:_cursor]
        for cid in remaining:
            if len(out) >= limit:
                break
            if cid in _priority:
                continue
            out.append({"client_id": cid, "source": "rotation", "position": len(out)})
    return out[:limit]


async def _await_job(job) -> str:
    """Wait for a job this engine started; return its final status.

    Deliberately NOT `await job.task`. An operator hitting Stop on the
    Live Results / Clients page cancels that exact task, and awaiting a
    cancelled task re-raises CancelledError in the *awaiter* -- which
    `_process_client` and `_worker` both re-raise (correctly, that is how
    engine shutdown propagates). Net effect: one Stop click permanently
    killed the round-robin slot for the rest of the process's life, while
    `status()` kept reporting it as a live slot, so the engine silently ran
    with fewer and fewer workers until a restart.

    `asyncio.wait` waits for the task without adopting its cancellation,
    yet still raises CancelledError if THIS coroutine is the one being
    cancelled (engine stop) -- exactly the distinction that was missing.
    """
    if job.task:
        await asyncio.wait({job.task})
    return job.status


async def _next_client_id() -> Optional[str]:
    """Pop the next client id off the rotation, refetching the client list
    from Mongo once per full lap so clients added/edited/removed take
    effect without a restart. Returns None if there are currently no
    clients with any keywords set."""
    global _cursor, _rotation
    async with _rotation_lock:
        # An admin's explicit "run next" jumps every lap boundary.
        while _priority:
            client_id = _priority.pop(0)
            if await _is_schedulable(client_id):
                return client_id
        if not _rotation:
            from backend.database.repositories import client_repository as clients_db

            clients = await clients_db.list_all()
            _rotation = [
                c["client_id"] for c in clients
                if (c.get("name_keywords") or c.get("domain_keywords"))
                and c.get("scheduler_enabled", True)
                and _due_for_discovery(c)
            ]
            _cursor = 0
        if not _rotation:
            return None
        client_id = _rotation[_cursor]
        _cursor += 1
        if _cursor >= len(_rotation):
            # end of a lap, force a fresh client list on the next pull
            _cursor = 0
            _rotation = []
        return client_id


async def _process_client(slot: int, client_id: str) -> Optional[str]:
    """Returns the recorded run status ("success" | "failed" | "skipped"),
    or None if the client vanished/lost its keywords between being queued
    and picked up (nothing to record)."""
    global _breaker_open, _avg_duration_s

    from backend.database.repositories import client_repository as clients_db
    from backend.platforms import registry
    from backend.services.job_service import CANCELLED, DISCOVERY, DONE, job_manager

    start = time.monotonic()
    _slot_state[slot] = {"client_id": client_id, "phase": "checking", "since": _now_iso()}

    client = await clients_db.try_get(client_id)
    if not client or not (client.get("name_keywords") or client.get("domain_keywords")):
        # edited/deleted between being queued and being picked up
        _slot_state[slot] = {"client_id": None, "phase": "idle", "since": _now_iso()}
        return None

    ready, unavailable = await registry.ready_platforms()
    await notify_unavailable_platforms(ready, unavailable)
    if not ready:
        detail = ", ".join(f"{p}={s}" for p, s in sorted(unavailable.items())) or "no platforms enabled"
        if not _breaker_open:
            _breaker_open = True
            log.error(f"round-robin suspended: no platform has a usable session ({detail})")
            from backend.services import incident_service as incidents_engine

            await incidents_engine.record(
                "all", "discovery", "-- round robin --", "round-robin-breaker",
                "PoolExhausted",
                f"The automatic round-robin engine is suspended because no platform has a usable "
                f"session ({detail}). Clients are not being scraped. It will resume automatically "
                "once any platform's session pool recovers.",
            )
        await clients_db.record_run_result(
            client_id, "skipped", f"no platform ready ({detail})", duration_s=time.monotonic() - start,
        )
        _slot_state[slot] = {"client_id": None, "phase": "idle", "since": _now_iso()}
        return "skipped"
    if _breaker_open:
        _breaker_open = False
        log.info(f"round-robin resumed: {', '.join(ready)} usable again")

    keywords = (client.get("name_keywords") or []) + (client.get("domain_keywords") or [])
    discovery_ok = True
    aborted = False
    _slot_state[slot]["phase"] = "discovery"
    try:
        job = job_manager.create(DISCOVERY, client_id, {
            "keywords": keywords, "tabs": ["people", "pages", "groups"], "max_results": 0, "max_seconds": 1800,
        })
        _engine_job_ids.add(job.id)
        try:
            job_status = await _await_job(job)
        finally:
            _engine_job_ids.discard(job.id)
        aborted = job_status == CANCELLED
        discovery_ok = job_status == DONE
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.error(f"round-robin discovery for {client_id}: {type(e).__name__}: {e}")
        discovery_ok = False

    # NO ANALYSIS PHASE, by explicit product decision. This engine used to
    # follow each sweep with an analysis catch-up for the same client.
    # Analysis is now driven solely by an analyst validating a profile
    # (profile_service queues it on approval), so the engine must never
    # start one on its own: it would analyse on the engine's schedule
    # rather than on the analyst's decision, and spend session budget on
    # profiles nobody has triaged yet.
    #
    # The approve -> analyse trigger keeps its backstop in
    # scheduler_service.catch_up_analysis, which only ever picks up
    # profiles an analyst ALREADY approved -- it retries the analyst's
    # decision, it does not make one.

    if aborted:
        # A human pressing Stop is not a symptom of anything, so it must not
        # feed _consecutive_failures -- five deliberate aborts in a row would
        # otherwise trip the "job worker spawn is degraded" incident and put
        # every slot into a 2-minute backoff for no reason at all.
        status = "skipped"
        note = "stopped by an operator from the UI"
        log.info(f"round-robin: {client_id} turn aborted by an operator")
    else:
        status = "success" if discovery_ok else "failed"
        note = "" if status == "success" else "discovery did not complete cleanly -- see Incidents"
    duration = time.monotonic() - start
    await clients_db.record_run_result(client_id, status, note, duration_s=duration)

    _avg_duration_s = _avg_duration_s * 0.8 + duration * 0.2
    _slot_state[slot] = {"client_id": None, "phase": "idle", "since": _now_iso()}
    return status


_worker_crash_incident_open: dict[int, bool] = {}


async def _worker(slot: int) -> None:
    global _consecutive_failures, _failure_incident_open

    while True:
        try:
            client_id = await _next_client_id()
            if client_id is None:
                # no clients have keywords set yet, poll rather than spin
                await asyncio.sleep(30)
                continue

            result = await _process_client(slot, client_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # _process_client already catches everything inside the
            # discovery/analysis phases it runs; this is for whatever it
            # DOESN'T guard (a Mongo hiccup in clients_db.try_get/
            # record_run_result, registry.ready_platforms(), etc). Without
            # this try/except, that exception propagates straight out of
            # this coroutine: asyncio.create_task() doesn't restart a dead
            # task on its own, so this slot would silently stop picking up
            # any client ever again, one fewer worker, forever, with no
            # error surfaced anywhere an operator would see it. Catching
            # here, logging, notifying, and looping is what actually makes
            # "the engine runs continuously" true rather than assumed.
            log.error(f"round-robin slot {slot}: unhandled {type(e).__name__}: {e}", exc_info=True)
            if not _worker_crash_incident_open.get(slot):
                _worker_crash_incident_open[slot] = True
                from backend.services import incident_service as incidents_engine

                await incidents_engine.record(
                    "all", "discovery", "-- round robin --", f"round-robin-slot-{slot}",
                    "RoundRobinWorkerError",
                    f"Round-robin worker slot {slot} hit an unexpected {type(e).__name__} outside its "
                    f"normal discovery/analysis error handling: {e}. The slot is recovering "
                    "automatically and will keep picking up clients -- this alert exists so an "
                    "operator knows it happened, since a slot silently and permanently dropping out "
                    "would otherwise look identical to the engine just running slower.",
                )
            _slot_state[slot] = {"client_id": None, "phase": "idle", "since": _now_iso()}
            await asyncio.sleep(30)
            continue
        else:
            _worker_crash_incident_open[slot] = False

        if result == "failed":
            _consecutive_failures += 1
            if _consecutive_failures >= FAILURE_INCIDENT_THRESHOLD and not _failure_incident_open:
                _failure_incident_open = True
                log.error(
                    f"round-robin: {_consecutive_failures} client turns in a row have failed -- "
                    "backing off, this looks systemic rather than one client's own problem"
                )
                from backend.services import incident_service as incidents_engine

                await incidents_engine.record(
                    "all", "discovery", "-- round robin --", "round-robin-failure-storm",
                    "RepeatedJobFailure",
                    f"{_consecutive_failures} client turns in a row failed to complete (each job's "
                    "worker process ended without reporting success). This usually means the job "
                    "worker spawn mechanism itself is degraded (e.g. an OS resource limit hit from "
                    "spawning many jobs back to back), not that any one client's keywords or session "
                    "are the problem. The engine is backing off between turns rather than retrying "
                    "immediately; check the Incidents list for the underlying error and consider "
                    "restarting the backend process if it doesn't recover on its own.",
                )
            # exponential backoff, capped, gives whatever resource was
            # exhausted (see the module docstring) real time to free up
            # instead of immediately re-attempting and burning through it
            # again on the very next slot iteration
            backoff = min(MAX_BACKOFF_S, 2 ** min(_consecutive_failures, 10))
            await asyncio.sleep(backoff)
        elif result == "success":
            if _consecutive_failures:
                log.info(f"round-robin: recovered after {_consecutive_failures} consecutive failure(s)")
            _consecutive_failures = 0
            _failure_incident_open = False
        elif result == "skipped":
            # No platform had a usable session for this client, _breaker_open
            # (above) already logs/alerts once for the whole outage, but
            # without a pause here the loop still re-checks the NEXT client
            # immediately, and the one after that, as fast as Mongo will
            # answer: with everything down, every client in the rotation
            # skips in a tight loop, burning a full CPU core per slot and
            # hammering Mongo with `record_run_result` writes for no reason
            #, there's nothing to do until some session recovers. `failed`
            # already backs off (loop above); `skipped` needs the same
            # courtesy, just a flat pause rather than an escalating one,
            # since "no session ready" isn't a symptom that gets worse the
            # longer it's ignored the way a spawn-failure storm is.
            await asyncio.sleep(5)


def start() -> None:
    global _running
    if _running:
        return
    _running = True
    n = max(1, settings.round_robin_slots)
    for slot in range(n):
        _slot_state[slot] = {"client_id": None, "phase": "idle", "since": _now_iso()}
        _tasks.append(asyncio.create_task(_worker(slot)))
    log.info(f"round-robin engine started with {n} slot(s)")


def stop() -> None:
    global _running, _consecutive_failures, _failure_incident_open
    if not _running:
        return
    _running = False
    for t in _tasks:
        t.cancel()
    _tasks.clear()
    _slot_state.clear()
    # Cancelling a worker task only unblocks the WORKER -- the job it was
    # waiting on is a separate task with its own child process and keeps
    # scraping regardless. Without this, "pause the engine" left Chrome
    # instances running to completion behind a Scheduler tab that said
    # stopped, and the next Resume stacked new jobs on top of them.
    from backend.services.job_service import job_manager

    for job_id in list(_engine_job_ids):
        if job_manager.cancel(job_id):
            log.info(f"round-robin stop: aborted in-flight job {job_id}")
    _engine_job_ids.clear()
    # a manual pause is a fresh start, not "still recovering", don't carry
    # a stale failure streak (or its backoff) into the next Resume
    _consecutive_failures = 0
    _failure_incident_open = False
    _worker_crash_incident_open.clear()
    log.info("round-robin engine stopped")


def owns_job(job_id: str) -> bool:
    """Whether the round-robin engine started this job, as opposed to an
    analyst clicking Discover/Analyse. The Scheduler tab's queue labels
    them differently: stopping one of its own is what the engine has to
    survive without losing a slot."""
    return job_id in _engine_job_ids


def status() -> dict:
    """Live engine state for the Scheduler admin tab and its start/stop
    toggle."""
    return {
        "running": _running,
        "slots": len(_tasks),
        "avg_duration_seconds": round(_avg_duration_s),
        "rotation_size": len(_rotation) or None,
        "current": list(_slot_state.values()),
        "consecutive_failures": _consecutive_failures,
        # whether THIS process auto-started the engine on boot (see
        # main.py's lifespan), distinct from `running`, which is the
        # engine's state right now regardless of how it got there. Lets the
        # Scheduler tab's toggle show "will auto-start next boot" even
        # while an admin has it manually paused/resumed in the meantime.
        "autostart": settings.scheduler_autostart,
    }


def set_autostart(enabled: bool) -> bool:
    """Persists whether the engine should start itself on the NEXT process
    boot (see main.py's lifespan), does not itself start or stop the
    engine right now; use POST /scheduler/start|stop for that."""
    from backend.config.settings import write_env

    write_env("SCHEDULER_AUTOSTART", "true" if enabled else "false")
    settings.scheduler_autostart = enabled
    return settings.scheduler_autostart


async def client_statuses() -> list[dict]:
    """Every client with keywords set, its last-run fields, and a rough ETA
    until the round-robin engine reaches it again, feeds the Scheduler
    admin tab. The ETA (rotation distance / slot count * average per-client
    duration) is an estimate, not a promise, a client's own runtime and
    any platform outages both move it around.

    `current_phase` is this client's row in `_slot_state` (see `status()`)
    merged straight in, keyed by client id; so the Scheduler tab can show
    "running now" directly on a client's own row instead of needing to
    cross-reference the separate per-slot `current` list itself."""
    from backend.database.repositories import client_repository as clients_db

    clients = [
        c for c in await clients_db.list_all()
        if c.get("name_keywords") or c.get("domain_keywords")
    ]
    queue_index = {cid: i for i, cid in enumerate(_priority)}
    n_slots = len(_tasks) if _running and _tasks else max(1, settings.round_robin_slots)
    rotation_snapshot = list(_rotation)
    running_now = {
        s["client_id"]: s for s in _slot_state.values() if s.get("client_id")
    }
    out = []
    for c in clients:
        eta_seconds: Optional[int] = None
        if not c.get("scheduler_enabled", True):
            pass  # parked: the engine will never reach it, so there is no ETA
        elif rotation_snapshot and c["client_id"] in rotation_snapshot:
            idx = rotation_snapshot.index(c["client_id"])
            distance = (idx - _cursor) % len(rotation_snapshot)
            eta_seconds = round((distance / n_slots) * _avg_duration_s)
        running = running_now.get(c["client_id"])
        out.append({
            "client_id": c["client_id"],
            "name": c.get("name") or c["client_id"],
            "last_run_at": c.get("last_run_at"),
            "last_run_status": c.get("last_run_status"),
            "last_run_note": c.get("last_run_note", ""),
            "last_run_duration_s": c.get("last_run_duration_s"),
            "run_count": c.get("run_count", 0),
            "eta_seconds": eta_seconds,
            "current_phase": running["phase"] if running else None,
            "current_since": running["since"] if running else None,
            # False = parked by an admin; the engine skips it entirely.
            # Manual runs from the Clients page still work.
            "scheduler_enabled": c.get("scheduler_enabled", True),
            # 0-based place in the admin-controlled queue, null if it is
            # only in the normal rotation
            "queue_position": queue_index.get(c["client_id"]),
        })
    return out
