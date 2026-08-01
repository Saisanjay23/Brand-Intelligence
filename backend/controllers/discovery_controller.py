"""`POST /discovery` is the engine's front door: the caller supplies its
own `client_id`/`client_name`/`keywords`, this upserts the client record
(so the name is on file for the final response, see `profile_controller.py`)
and launches one job that sweeps every platform with a ready session.
"""

from __future__ import annotations

from backend.dto.discovery_dto import DiscoveryIn
from backend.services import client_service
from backend.services.job_service import DISCOVERY, job_manager


async def start_discovery(body: DiscoveryIn) -> dict:
    await client_service.upsert(body.client_id, body.client_name, body.keywords)

    params: dict = {"keywords": body.keywords, "tabs": body.tabs, "max_results": body.max_results}
    if body.max_seconds is not None:
        params["max_seconds"] = body.max_seconds
    if body.concurrency is not None:
        params["concurrency"] = body.concurrency

    job = job_manager.create(DISCOVERY, body.client_id, params, callback_url=body.callback_url)
    return {"job_id": job.id, "status": job.status}
