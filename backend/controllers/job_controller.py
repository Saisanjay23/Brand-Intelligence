"""Polling surface for a job started by `POST /discovery` or
`POST /analysis`. `after_seq` lets a caller resume an events stream after
a gap (a dropped connection, a restart) without missing anything, since
event sequence numbers only ever increase.
"""

from __future__ import annotations

from backend.services.job_service import TERMINAL, job_manager
from backend.shared.errors import ConflictError, NotFoundError


def _get(job_id: str):
    job = job_manager.get(job_id)
    if job is None:
        raise NotFoundError(f"job {job_id!r} not found")
    return job


def _to_dict(job) -> dict:
    out = job.to_dict()
    blocker = job_manager.blocking_job(job)
    out["blocked_by"] = (
        {"job_id": blocker.id, "client_id": blocker.client_id, "kind": blocker.kind, "platform": blocker.platform}
        if blocker else None
    )
    return out


async def list_jobs(client_id: str = "", limit: int = 25) -> dict:
    return {"items": [_to_dict(j) for j in job_manager.recent(limit, client_id)]}


async def get_job(job_id: str) -> dict:
    return _to_dict(_get(job_id))


async def get_job_events(job_id: str, after_seq: int = 0) -> dict:
    job = _get(job_id)
    events = [e.to_dict() for e in job.events if e.seq > after_seq]
    return {"items": events, "last_seq": job.events[-1].seq if job.events else 0}


async def cancel_job(job_id: str) -> dict:
    job = _get(job_id)
    ok = await job_manager.cancel(job_id)
    if not ok:
        # distinct from a 404 (the job id is real), the current state just
        # doesn't allow a cancel, e.g. it's already DONE/FAILED/CANCELLED.
        # A caller that doesn't inspect a 200 body would otherwise believe a
        # stale/duplicate cancel click had succeeded.
        if job.status in TERMINAL:
            raise ConflictError(f"job {job_id!r} already {job.status} -- nothing to cancel")
        raise ConflictError(f"job {job_id!r} cannot be cancelled right now")
    return {"cancelled": True}
