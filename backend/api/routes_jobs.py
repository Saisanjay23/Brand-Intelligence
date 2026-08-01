"""Polling surface for a job started by `POST /discovery` or
`POST /analysis`. `after_seq` lets a caller resume an events stream after
a gap (a dropped connection, a restart) without missing anything, since
event sequence numbers only ever increase.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.engine.jobs import job_manager
from backend.shared.errors import NotFoundError

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _get(job_id: str):
    job = job_manager.get(job_id)
    if job is None:
        raise NotFoundError(f"job {job_id!r} not found")
    return job


@router.get("")
async def list_jobs(client_id: str = "", limit: int = 25) -> dict:
    return {"items": [j.to_dict() for j in job_manager.recent(limit, client_id)]}


@router.get("/{job_id}")
async def get_job(job_id: str) -> dict:
    return _get(job_id).to_dict()


@router.get("/{job_id}/events")
async def get_job_events(job_id: str, after_seq: int = 0) -> dict:
    job = _get(job_id)
    events = [e.to_dict() for e in job.events if e.seq > after_seq]
    return {"items": events, "last_seq": job.events[-1].seq if job.events else 0}


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    _get(job_id)
    ok = await job_manager.cancel(job_id)
    return {"cancelled": ok}
