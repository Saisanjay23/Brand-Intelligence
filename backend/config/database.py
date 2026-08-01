"""Mongo connection lifecycle -- the one place a Motor client is created.

One database (`settings.mongo_db_name`), collections `clients`, `profiles`,
`sessions`, `session_health`, `session_item_health`, `incidents` -- see
docs/adr/0004 for why this isn't split one-database-per-platform.

Naive (non-tz-aware) datetimes are used deliberately: PyMongo without
tz_aware hands back naive-but-UTC-VALUED datetimes, and `repositories/profile_repository.py`
compares Mongo-sourced datetimes against other naive-but-UTC ones
internally. Marking a value tz-aware for API output is `repositories/*.py`'s own job
when it serializes a document out, not this client's.
"""

from __future__ import annotations

from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from backend.config.settings import settings
from backend.utils.logging import get_logger

log = get_logger("database")

_client: Optional[AsyncIOMotorClient] = None


def client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
        log.info(f"mongo connected: {settings.mongo_uri}")
    return _client


def db() -> AsyncIOMotorDatabase:
    return client()[settings.mongo_db_name]


async def ping() -> bool:
    try:
        await client().admin.command("ping")
        return True
    except Exception as e:
        log.warning(f"mongo unreachable: {type(e).__name__}: {e}")
        return False


async def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
