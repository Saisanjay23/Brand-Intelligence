from __future__ import annotations

from fastapi import APIRouter

from backend.controllers import analysis_controller
from backend.dto.analysis_dto import AnalysisIn

router = APIRouter(tags=["analysis"])


@router.post("/analysis", status_code=202)
async def start_analysis(body: AnalysisIn) -> dict:
    return await analysis_controller.start_analysis(body)
