"""Client persistence -- the `clients` collection, one document per
caller-supplied `client_id` (your SaaS's own customer/org id, passed
straight through and used as-is, never regenerated).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.shared.errors import NotFoundError
from backend.database.connection import db

CLIENTS = "clients"


def _to_out(doc: dict) -> dict:
    return {
        "client_id": doc["_id"],
        "name": doc.get("name", ""),
        "domain": doc.get("domain", ""),
        # two deliberately separate curated lists, not one merged bag --
        # individual names (people to protect) and domain/brand keyword
        # variants are different kinds of search terms an analyst tunes
        # independently. Combined at search time, never merged in storage.
        "name_keywords": doc.get("name_keywords", []),
        "domain_keywords": doc.get("domain_keywords", []),
        "cron": doc.get("cron"),
        "created_at": doc.get("created_at"),
    }


async def upsert(
    client_id: str, name: str, domain: str = "",
    name_keywords: Optional[list[str]] = None, domain_keywords: Optional[list[str]] = None,
    cron: Optional[str] = None,
) -> dict:
    """`cron` is optional -- a client with keywords but no cron only ever
    gets swept when `POST /discovery` is called for it explicitly; setting
    cron additionally schedules an automatic recurring sweep (see
    sessions/manager.py / services/scheduler_service.py)."""
    now = datetime.now(timezone.utc)
    await db()[CLIENTS].update_one(
        {"_id": client_id},
        {
            "$set": {
                "name": name, "domain": domain,
                "name_keywords": name_keywords or [], "domain_keywords": domain_keywords or [],
                "cron": cron,
            },
            "$setOnInsert": {"_id": client_id, "created_at": now},
        },
        upsert=True,
    )
    doc = await db()[CLIENTS].find_one({"_id": client_id})
    return _to_out(doc)


async def get(client_id: str) -> dict:
    doc = await db()[CLIENTS].find_one({"_id": client_id})
    if doc is None:
        raise NotFoundError(f"client {client_id!r} not found")
    return _to_out(doc)


async def try_get(client_id: str) -> Optional[dict]:
    """Like `get`, but returns None instead of raising -- for internal
    engine code that needs to check existence without a 404 semantics."""
    doc = await db()[CLIENTS].find_one({"_id": client_id})
    return _to_out(doc) if doc else None


async def list_all() -> list[dict]:
    """Every client -- used by the scheduler's cron sync and the analysis
    catch-up sweep, which operate across all of them."""
    return [_to_out(d) async for d in db()[CLIENTS].find({})]


async def delete(client_id: str) -> dict:
    doc = await db()[CLIENTS].find_one_and_delete({"_id": client_id})
    if doc is None:
        raise NotFoundError(f"client {client_id!r} not found")
    return _to_out(doc)


async def ensure_indexes() -> None:
    pass  # _id is already the unique key; nothing extra to index here
