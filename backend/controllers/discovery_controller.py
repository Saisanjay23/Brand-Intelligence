"""`POST /discovery` sweeps every ready platform for one already-existing
client's keywords (see `dto/discovery_dto.py`). The client itself is
created/configured separately via `POST /clients` -- this no longer
upserts it as a side effect, since a client's org id/name/domain/keyword
config is now curated up front, not inferred from whatever a discovery
call happened to be called with.
"""

from __future__ import annotations

from backend.dto.discovery_dto import DiscoveryIn
from backend.services import client_service
from backend.services.job_service import DISCOVERY, job_manager


async def start_discovery(body: DiscoveryIn) -> dict:
    await client_service.get(body.client_id)  # 404s if the client was never configured

    params: dict = {"keywords": body.keywords, "tabs": body.tabs, "max_results": body.max_results}
    if body.max_seconds is not None:
        params["max_seconds"] = body.max_seconds
    if body.concurrency is not None:
        params["concurrency"] = body.concurrency

    job = job_manager.create(DISCOVERY, body.client_id, params, callback_url=body.callback_url)
    return {"job_id": job.id, "status": job.status}
