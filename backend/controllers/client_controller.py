"""`POST /clients` is a plain upsert -- the canonical way a client's org
id/name/domain/keyword config gets created or edited (see
`dto/client_dto.py`). Discovery no longer upserts a client as a side
effect: a client must exist (created here) before it can be searched.
"""

from __future__ import annotations

from backend.dto.client_dto import ClientIn
from backend.services import client_service
from backend.services import scheduler_service as scheduler


async def upsert_client(body: ClientIn) -> dict:
    out = await client_service.upsert(
        body.client_id, body.name, body.domain, body.name_keywords, body.domain_keywords,
        body.platform_limits, body.platform_tab_limits, body.cron,
        body.name_keyword_drk, body.domain_keyword_drk,
    )
    scheduler.sync()
    return out


async def get_client(client_id: str) -> dict:
    return await client_service.get(client_id)


async def list_clients() -> dict:
    return {"items": await client_service.list_all()}


async def delete_client(client_id: str) -> dict:
    out = await client_service.delete(client_id)
    scheduler.sync()
    return out
