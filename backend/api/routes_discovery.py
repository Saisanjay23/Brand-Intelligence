"""`POST /discovery` is the engine's front door: the caller supplies its
own `client_id`/`client_name`/`keywords`, this upserts the client record
(so the name is on file for the final response, see `routes_profiles.py`)
and launches one job that sweeps every platform with a ready session.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.db import clients as clients_db
from backend.engine.jobs import DISCOVERY, job_manager

router = APIRouter(tags=["discovery"])


class DiscoveryIn(BaseModel):
    client_id: str
    client_name: str
    keywords: list[str]
    tabs: list[str] = ["people", "pages"]
    max_results: int = 0
    max_seconds: Optional[float] = None
    concurrency: Optional[int] = None
    callback_url: str = ""


@router.post("/discovery", status_code=202)
async def start_discovery(body: DiscoveryIn) -> dict:
    await clients_db.upsert(body.client_id, body.client_name, body.keywords)

    params: dict = {"keywords": body.keywords, "tabs": body.tabs, "max_results": body.max_results}
    if body.max_seconds is not None:
        params["max_seconds"] = body.max_seconds
    if body.concurrency is not None:
        params["concurrency"] = body.concurrency

    job = job_manager.create(DISCOVERY, body.client_id, params, callback_url=body.callback_url)
    return {"job_id": job.id, "status": job.status}
