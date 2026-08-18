from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.controllers import scheduler_controller

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


class AutostartIn(BaseModel):
    enabled: bool


class EnqueueIn(BaseModel):
    client_id: str
    # True = "run next" (head of the queue), False = append behind whatever
    # an admin has already queued
    front: bool = True


class MoveIn(BaseModel):
    direction: str  # "up" | "down"


class EnabledIn(BaseModel):
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


@router.get("/queue")
async def get_queue(limit: int = Query(12, ge=1, le=100)) -> dict:
    """What the engine is running right now and what it will pick up next.

    Polled far more often than /scheduler/status (which reads every client
    out of Mongo); this one is in-memory only.
    """
    return await scheduler_controller.get_queue(limit)


@router.post("/queue")
async def enqueue(body: EnqueueIn) -> dict:
    return await scheduler_controller.enqueue(body.client_id, front=body.front)


@router.post("/queue/{client_id}/move")
async def move_in_queue(client_id: str, body: MoveIn) -> dict:
    return await scheduler_controller.move_in_queue(client_id, body.direction)


@router.delete("/queue/{client_id}")
async def dequeue(client_id: str) -> dict:
    """Drop a client from the queue. Does not stop a turn already in
    flight for it -- that is POST /jobs/{job_id}/cancel."""
    return await scheduler_controller.dequeue(client_id)


@router.put("/clients/{client_id}/enabled")
async def set_client_enabled(client_id: str, body: EnabledIn) -> dict:
    """Park a client out of the rotation entirely, or put it back. Manual
    Discover/Analyse runs are unaffected either way."""
    return await scheduler_controller.set_client_enabled(client_id, body.enabled)
