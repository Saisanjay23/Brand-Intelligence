from __future__ import annotations

from fastapi import APIRouter

from backend.controllers import job_controller

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(client_id: str = "", limit: int = 25) -> dict:
    return await job_controller.list_jobs(client_id, limit)


@router.get("/{job_id}")
async def get_job(job_id: str) -> dict:
    return await job_controller.get_job(job_id)


@router.get("/{job_id}/events")
async def get_job_events(job_id: str, after_seq: int = 0) -> dict:
    return await job_controller.get_job_events(job_id, after_seq)


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    return await job_controller.cancel_job(job_id)
