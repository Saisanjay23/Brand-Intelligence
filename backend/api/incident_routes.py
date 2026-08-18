"""Operational incidents -- what the pipeline itself is struggling with.

Distinct from `published_incident_routes.py`, which serves the CLIENT
deliverable (an impersonating account we found). These are OUR failures:
a dead session, a parser that stopped recognising a page, a field that
stopped extracting. Until this existed they were only visible by email or
by reading Mongo directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.database.repositories import incident_repository as incidents_db

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _out(doc: dict) -> dict:
    ts = doc.get("ts")
    return {
        "id": str(doc.get("_id", "")),
        "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts or ""),
        "platform": doc.get("platform", ""),
        "kind": doc.get("kind", ""),
        "scope": doc.get("scope", ""),
        "job_id": doc.get("job_id", ""),
        "error_type": doc.get("error_type", ""),
        "severity": doc.get("severity", ""),
        "message": doc.get("message", ""),
        "cause": doc.get("cause", ""),
        "fix": doc.get("fix", ""),
        "where": doc.get("where", ""),
        "url": doc.get("url", ""),
    }


@router.get("")
async def list_incidents(
    limit: int = Query(50, ge=1, le=500),
    severity: str = "",
    platform: str = "",
) -> dict:
    items = await incidents_db.recent(limit=limit, severity=severity, platform=platform)
    return {"items": [_out(d) for d in items], "counts": await incidents_db.counts_by_severity()}
