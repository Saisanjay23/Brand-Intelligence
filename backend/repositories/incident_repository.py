"""Incident persistence -- the `incidents` collection, TTL-bounded. Kept in
Mongo rather than memory: unlike the health tracker's rolling window, an
incident is exactly the kind of thing a person comes back the next day to
ask "why did last night's run fail" -- losing it on every restart would
defeat the point. The TTL still bounds it automatically.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from backend.config.database import db

INCIDENTS = "incidents"
RETENTION_DAYS = 14


async def record(doc: dict) -> None:
    try:
        await db()[INCIDENTS].insert_one(doc)
    except Exception:
        pass  # the incident log itself must never be why a job fails


async def recent(limit: int = 50, platform: Optional[str] = None) -> list[dict]:
    q = {"platform": platform} if platform else {}
    out = []
    async for d in db()[INCIDENTS].find(q).sort("ts", -1).limit(limit):
        d["id"] = str(d.pop("_id"))
        out.append(d)
    return out


async def since(ts: datetime) -> list[dict]:
    return await db()[INCIDENTS].find({"ts": {"$gte": ts}}).to_list(length=1000)


async def ensure_indexes() -> None:
    await db()[INCIDENTS].create_index("ts", expireAfterSeconds=RETENTION_DAYS * 86400, name="ttl_ts")
