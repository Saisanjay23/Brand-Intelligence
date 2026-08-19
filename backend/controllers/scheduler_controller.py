"""Control surface for the round-robin engine: its own start/stop, the
queue of what it will run next, and the per-client toggle that parks a
client out of the rotation.

The "queue" an admin sees on the Scheduler tab is two different things
stitched together, and the distinction matters for what can be done to
each:

  * live JOBS (running or waiting on a platform lock) -- these are real
    work in flight. They can be stopped, which is a job cancel.
  * UPCOMING CLIENTS (the admin's own priority list, then the remainder of
    the current lap) -- these have not started. They can be reordered or
    dropped, which only touches the engine's in-memory queue.
"""

from __future__ import annotations

from backend.database.repositories import client_repository as clients_db
from backend.services import round_robin_service
from backend.services.job_service import TERMINAL, job_manager
from backend.shared.errors import ConflictError, NotFoundError


async def get_status() -> dict:
    engine = round_robin_service.status()
    clients = await round_robin_service.client_statuses()
    return {**engine, "clients": clients}


async def start_engine() -> dict:
    round_robin_service.start()
    return round_robin_service.status()


async def stop_engine() -> dict:
    round_robin_service.stop()
    return round_robin_service.status()


async def _client_names() -> dict[str, str]:
    return {c["client_id"]: c.get("name") or c["client_id"] for c in await clients_db.list_all()}


async def get_queue(limit: int = 12) -> dict:
    """Everything the engine is doing or is about to do, in one payload.

    `jobs` is ordered running-first: an admin scanning the panel wants the
    thing burning a session right now at the top, not sorted by id.
    """
    names = await _client_names()
    jobs = [
        {
            "job_id": j.id,
            "client_id": j.client_id,
            "client_name": names.get(j.client_id, j.client_id),
            "kind": j.kind,
            "platform": j.platform,
            "status": j.status,
            "message": j.message,
            "started": j.started,
            # who started it: the engine, or an analyst clicking Discover /
            # Analyse. Both share the same platform locks, so a manual run
            # is a legitimate reason for an engine job to be sitting in
            # `queued`, and vice versa -- saying which is which is the
            # difference between "the queue is stuck" and "it is waiting
            # its turn, as designed".
            "source": "scheduler" if round_robin_service.owns_job(j.id) else "manual",
            "blocked_by": (
                {"job_id": b.id, "client_id": b.client_id, "kind": b.kind, "platform": b.platform}
                if (b := job_manager.blocking_job(j)) else None
            ),
        }
        for j in job_manager.recent(200)
        if j.status not in TERMINAL
    ]
    jobs.sort(key=lambda j: (j["status"] != "running", j["started"] or ""))

    upcoming = [
        {**entry, "client_name": names.get(entry["client_id"], entry["client_id"])}
        for entry in round_robin_service.upcoming(limit)
    ]
    return {
        "running": round_robin_service.status()["running"],
        "jobs": jobs,
        "upcoming": upcoming,
        "queue": round_robin_service.priority_queue(),
    }


async def _require_client(client_id: str) -> dict:
    client = await clients_db.try_get(client_id)
    if client is None:
        raise NotFoundError(f"client {client_id!r} not found")
    return client


async def enqueue(client_id: str, *, front: bool) -> dict:
    """Add a client to the admin queue, at the front ("run next") or the
    back. Refused for a client the engine could never act on, so a queue
    entry that silently evaporates on its turn is impossible to create."""
    client = await _require_client(client_id)
    if not (client.get("name_keywords") or client.get("domain_keywords")):
        raise ConflictError(f"client {client_id!r} has no keywords -- nothing to run")
    if not client.get("scheduler_enabled", True):
        raise ConflictError(
            f"client {client_id!r} is paused for the scheduler -- re-enable it before queueing a run"
        )
    queue = round_robin_service.run_next(client_id) if front else round_robin_service.enqueue(client_id)
    return {"queue": queue}


async def move_in_queue(client_id: str, direction: str) -> dict:
    if direction not in ("up", "down"):
        raise ConflictError("direction must be 'up' or 'down'")
    try:
        return {"queue": round_robin_service.move(client_id, direction)}
    except ValueError as e:
        raise NotFoundError(str(e)) from e


async def dequeue(client_id: str) -> dict:
    return {"queue": round_robin_service.dequeue(client_id)}


async def set_client_enabled(client_id: str, enabled: bool) -> dict:
    """Park a client out of the rotation, or put it back.

    Disabling also drops any queue entry it had: leaving one behind would
    show a client sitting in the queue that `_next_client_id` then skips
    over on its turn, which reads as the queue being broken.
    """
    await _require_client(client_id)
    await clients_db.set_scheduler_enabled(client_id, enabled)
    if not enabled:
        round_robin_service.dequeue(client_id)
    return {"client_id": client_id, "scheduler_enabled": enabled}
