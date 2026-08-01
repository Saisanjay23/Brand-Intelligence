"""`POST /analysis` is the manual/catch-up trigger only -- the normal path
is automatic: approving a profile in `routes_profiles.py` auto-queues
analysis for that profile's own platform. This exists for an analyst who
wants to force a sweep of everything approved-but-unanalysed right now.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.engine.jobs import ANALYSIS, job_manager

router = APIRouter(tags=["analysis"])


class AnalysisIn(BaseModel):
    client_id: str
    callback_url: str = ""


@router.post("/analysis", status_code=202)
async def start_analysis(body: AnalysisIn) -> dict:
    job = job_manager.create(ANALYSIS, body.client_id, {}, callback_url=body.callback_url)
    return {"job_id": job.id, "status": job.status}
