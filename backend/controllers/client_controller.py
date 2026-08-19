"""`POST /clients` is a plain upsert, the canonical way a client's org
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
        body.asset_name_individual_keywords, body.asset_name_domain_keywords,
        body.platform_limits_individual, body.platform_limits_domain, body.platform_tab_limits, body.cron,
    )
    scheduler.sync()
    return out


async def get_client(client_id: str) -> dict:
    return await client_service.get(client_id)


async def list_clients() -> dict:
    return {"items": await client_service.list_all()}


async def reorder_clients(client_ids: list[str]) -> list[dict]:
    return await client_service.reorder(client_ids)


async def delete_client(client_id: str) -> dict:
    out = await client_service.delete(client_id)
    scheduler.sync()
    return out
