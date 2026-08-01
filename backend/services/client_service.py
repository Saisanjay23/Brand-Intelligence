"""Client business logic -- thin today (mostly a repository passthrough),
kept as its own service so controllers never call a repository directly,
per the layering rule (controller -> service -> repository).
"""

from __future__ import annotations

from typing import Optional

from backend.database.repositories import client_repository as clients_db


async def upsert(client_id: str, name: str, keywords: list[str], cron: Optional[str] = None) -> dict:
    return await clients_db.upsert(client_id, name, keywords, cron)


async def get(client_id: str) -> dict:
    return await clients_db.get(client_id)
