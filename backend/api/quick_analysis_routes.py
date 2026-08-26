from __future__ import annotations

from io import BytesIO
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.services.quick_analysis_service import quick_analysis_manager

router = APIRouter(prefix="/quick-analysis", tags=["quick-analysis"])


class QuickAnalysisStartRequest(BaseModel):
    urls: list[str] = Field(default_factory=list, min_length=1)
    target_name: Optional[str] = ""
    official_feed: Optional[str] = ""


class QuickAnalysisExportRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list, min_length=1)
    filename: Optional[str] = "quick_analysis.xlsx"


@router.post("/start")
async def start_quick_analysis(body: QuickAnalysisStartRequest) -> dict:
    if not body.urls:
        raise HTTPException(status_code=400, detail="No URLs provided")
    
    # Cap to 100 URLs per batch for memory/performance safety
    urls = [u.strip() for u in body.urls if u.strip()][:100]
    if not urls:
        raise HTTPException(status_code=400, detail="No valid URLs provided")

    job_id, skipped = quick_analysis_manager.start_job(
        urls=urls,
        target_name=body.target_name or "",
        official_feed=body.official_feed or "",
    )

    if not job_id:
        raise HTTPException(
            status_code=400,
            detail=f"None of the provided URLs could be processed. Skipped: {len(skipped)}",
        )

    return {
        "job_id": job_id,
        "skipped": skipped,
        "status": "queued",
    }


@router.get("/job/{job_id}")
async def get_quick_analysis_job(job_id: str) -> dict:
    job_data = quick_analysis_manager.get_job(job_id)
    if not job_data:
        raise HTTPException(status_code=404, detail="Quick analysis job not found or expired from RAM")
    return job_data


@router.post("/cancel/{job_id}")
async def cancel_quick_analysis_job(job_id: str) -> dict:
    cancelled = quick_analysis_manager.cancel_job(job_id)
    return {"cancelled": cancelled}


@router.get("/screenshot/{job_id}/{item_id}")
async def get_quick_analysis_screenshot(job_id: str, item_id: str):
    data = quick_analysis_manager.get_screenshot(job_id, item_id)
    if not data:
        raise HTTPException(status_code=404, detail="Screenshot not found in RAM")
    return Response(
        content=data,
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="screenshot_{item_id}.png"',
            "Cache-Control": "private, max-age=60",
        },
    )


@router.post("/export-xlsx")
async def export_quick_analysis_xlsx(body: QuickAnalysisExportRequest):
    if not body.rows:
        raise HTTPException(status_code=400, detail="No rows to export")

    from openpyxl import Workbook

    def _safe(v: object) -> object:
        if v is None:
            return ""
        if isinstance(v, (int, float, bool)):
            return v
        s = str(v).strip()
        if not s:
            return ""
        if s == "0" or (s.lstrip("-").isdigit() and not (len(s) > 1 and s.startswith("0"))):
            try:
                return int(s)
            except ValueError:
                pass
        elif re.match(r"^-?\d+\.\d+$", s):
            try:
                return float(s)
            except ValueError:
                pass
        # CWE-1236 CSV/Excel formula injection defense
        return f"'{s}" if s[:1] in ("=", "+", "-", "@") else s

    wb = Workbook()
    ws = wb.active
    ws.title = "Quick Analysis"
    cols = list(body.rows[0].keys())
    ws.append(cols)
    for row in body.rows:
        ws.append([_safe(row.get(c)) for c in cols])

    buf = BytesIO()
    wb.save(buf)
    filename = (body.filename or "quick_analysis.xlsx").replace('"', "")
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
