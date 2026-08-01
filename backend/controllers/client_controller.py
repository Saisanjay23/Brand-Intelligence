"""`POST /clients` is a plain upsert -- most callers never hit this
directly since `POST /discovery` upserts the client as a side effect, but
it's here for registering/renaming a client (and setting `cron`) without
also kicking off a sweep.
"""

from __future__ import annotations

from backend.dto.client_dto import ClientIn
from backend.services import client_service
from backend.services import scheduler_service as scheduler


async def upsert_client(body: ClientIn) -> dict:
    out = await client_service.upsert(body.client_id, body.name, body.keywords, body.cron)
    scheduler.sync()
    return out


async def get_client(client_id: str) -> dict:
    return await client_service.get(client_id)
