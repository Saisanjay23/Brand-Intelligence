from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from backend.engine import incidents as incidents_engine

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("")
async def list_incidents(limit: int = 50, platform: Optional[str] = None) -> dict:
    return {"items": await incidents_engine.recent(limit, platform)}
