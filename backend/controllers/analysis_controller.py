"""`POST /analysis` is the manual/catch-up trigger only -- the normal path
is automatic: approving a profile in `profile_controller.py` auto-queues
analysis for that profile's own platform. This exists for an analyst who
wants to force a sweep of everything approved-but-unanalysed right now.
"""

from __future__ import annotations

from backend.dto.analysis_dto import AnalysisIn
from backend.services.job_service import ANALYSIS, job_manager


async def start_analysis(body: AnalysisIn) -> dict:
    job = job_manager.create(ANALYSIS, body.client_id, {}, callback_url=body.callback_url)
    return {"job_id": job.id, "status": job.status}
