from __future__ import annotations

from fastapi import APIRouter

from backend.controllers import discovery_controller
from backend.dto.discovery_dto import DiscoveryIn

router = APIRouter(tags=["discovery"])


@router.post("/discovery", status_code=202)
async def start_discovery(body: DiscoveryIn) -> dict:
    return await discovery_controller.start_discovery(body)
