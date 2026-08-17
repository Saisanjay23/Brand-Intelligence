from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from backend.controllers import published_incident_controller
from backend.shared.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/published-incidents", tags=["published-incidents"])


@router.get("")
async def list_published_incidents(
    client_id: str,
    platform: Optional[str] = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict:
    return await published_incident_controller.list_published(client_id, platform, limit, offset)
