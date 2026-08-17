from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.controllers import scheduler_controller

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


class AutostartIn(BaseModel):
    enabled: bool


@router.get("/status")
async def get_status() -> dict:
    return await scheduler_controller.get_status()


@router.post("/start")
async def start_engine() -> dict:
    return await scheduler_controller.start_engine()


@router.post("/stop")
async def stop_engine() -> dict:
    return await scheduler_controller.stop_engine()


@router.put("/autostart")
async def set_autostart(body: AutostartIn) -> dict:
    """Whether the engine starts itself the next time this process boots
    does not start/stop it right now, see /scheduler/start|stop for that."""
    return {"autostart": scheduler_controller.set_autostart(body.enabled)}
