from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from backend.controllers import incident_controller

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("")
async def list_incidents(limit: int = 50, platform: Optional[str] = None) -> dict:
    return await incident_controller.list_incidents(limit, platform)
