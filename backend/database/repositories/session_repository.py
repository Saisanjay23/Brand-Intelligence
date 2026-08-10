"""Session pool persistence -- pooled login cookies (`sessions`) and cached
liveness results (`session_health`, `session_item_health`), replacing the
pre-rebuild `session/<platform>.json` files. `_id` = `"<platform>:<id>"` so
lookups/upserts never need a separate compound-unique index.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pymongo import ReturnDocument

from backend.database.connection import db

SESSIONS = "sessions"
SESSION_HEALTH = "session_health"
SESSION_ITEM_HEALTH = "session_item_health"


def _doc_id(platform: str, session_id: str) -> str:
    return f"{platform}:{session_id}"


def _to_item(doc: dict) -> dict:
    return {
        "platform": doc["platform"], "id": doc["session_id"],
        "identifier": doc.get("identifier", doc["session_id"]),
        "status": doc.get("status", "ready"),
        "cookies": doc.get("cookies", []), "proxy": doc.get("proxy"),
        "rate_limited_until": float(doc.get("rate_limited_until") or 0),
        "last_used": float(doc.get("last_used") or 0),
        # how many times this session has been handed to a job -- see
        # increment_use_count(), called only from sessions/manager.py's
        # get_healthy_session() at the moment a session is actually picked
        # for real use (not on every health check).
        "use_count": int(doc.get("use_count") or 0),
        # consecutive failures since this session last demonstrably worked --
        # drives the graduated quarantine ladder in sessions/manager.py.
        # Reset to 0 by mark_session_ok, never by simply handing it out.
        "consecutive_failures": int(doc.get("consecutive_failures") or 0),
        "quarantine_minutes": int(doc.get("quarantine_minutes") or 0),
        "api_key": doc.get("api_key", ""),
        "api_id": doc.get("api_id", ""),
        "api_hash": doc.get("api_hash", ""),
        "phone": doc.get("phone", ""),
        "session_blob": doc.get("session_blob"),
    }


async def list_pool(platform: str) -> list[dict]:
    return [_to_item(d) async for d in db()[SESSIONS].find({"platform": platform})]


async def count_pool(platform: str) -> int:
    return await db()[SESSIONS].count_documents({"platform": platform})


async def get_item(platform: str, session_id: str) -> Optional[dict]:
    doc = await db()[SESSIONS].find_one({"_id": _doc_id(platform, session_id)})
    return _to_item(doc) if doc else None


async def add_item(platform: str, cookies: list[dict], identifier: str, proxy: Optional[dict] = None) -> dict:
    if await count_pool(platform) >= 20:
        raise ValueError(f"Session pool capacity limit (20) reached for {platform}. Please update an expired session or delete one.")
    session_id = uuid.uuid4().hex[:8]
    doc = {
        "_id": _doc_id(platform, session_id), "platform": platform, "session_id": session_id,
        "identifier": identifier, "status": "ready", "cookies": cookies, "proxy": proxy,
        "rate_limited_until": 0.0, "last_used": 0.0,
    }
    await db()[SESSIONS].insert_one(doc)
    return _to_item(doc)


async def save_api_key_session(platform: str, key: str, identifier: str) -> dict:
    if await count_pool(platform) >= 20:
        raise ValueError(f"Session pool capacity limit (20) reached for {platform}. Please update an expired key or delete one.")
    session_id = uuid.uuid4().hex[:8]
    doc = {
        "_id": _doc_id(platform, session_id), "platform": platform, "session_id": session_id,
        "identifier": identifier, "status": "ready", "api_key": key, "cookies": [],
        "proxy": None, "rate_limited_until": 0.0, "last_used": 0.0,
    }
    await db()[SESSIONS].update_one({"_id": _doc_id(platform, session_id)}, {"$set": doc}, upsert=True)
    return _to_item(doc)


async def save_mtproto_session(platform: str, identifier: str, api_id: int, api_hash: str, phone: str, session_blob: Optional[bytes]) -> dict:
    session_id = "mtproto"
    doc = {
        "_id": _doc_id(platform, session_id), "platform": platform, "session_id": session_id,
        "identifier": identifier, "status": "ready", "api_id": api_id, "api_hash": api_hash,
        "phone": phone, "session_blob": session_blob, "cookies": [], "proxy": None,
        "rate_limited_until": 0.0, "last_used": 0.0,
    }
    await db()[SESSIONS].update_one({"_id": _doc_id(platform, session_id)}, {"$set": doc}, upsert=True)
    return _to_item(doc)


async def update_session_credentials(platform: str, session_id: str, **credentials) -> Optional[dict]:
    fields = {"status": "ready", "rate_limited_until": 0.0}
    for k in ("cookies", "api_key", "identifier", "api_id", "api_hash", "phone", "session_blob"):
        if k in credentials and credentials[k] is not None:
            fields[k] = credentials[k]
    res = await db()[SESSIONS].update_one({"_id": _doc_id(platform, session_id)}, {"$set": fields})
    if res.matched_count == 0:
        return None
    return await get_item(platform, session_id)


async def update_item(platform: str, session_id: str, **fields) -> bool:
    res = await db()[SESSIONS].update_one({"_id": _doc_id(platform, session_id)}, {"$set": fields})
    return res.matched_count > 0


async def increment_use_count(platform: str, session_id: str) -> int:
    """Atomic +1, returning the new total -- a $set of a value read back
    earlier would race two concurrent round-robin slots picking the same
    just-freed session and silently drop one of the two increments."""
    doc = await db()[SESSIONS].find_one_and_update(
        {"_id": _doc_id(platform, session_id)},
        {"$inc": {"use_count": 1}},
        return_document=ReturnDocument.AFTER,
    )
    return int(doc.get("use_count") or 0) if doc else 0


async def unset_proxy(platform: str, session_id: str) -> bool:
    res = await db()[SESSIONS].update_one({"_id": _doc_id(platform, session_id)}, {"$unset": {"proxy": ""}})
    return res.matched_count > 0


async def delete_item(platform: str, session_id: str) -> bool:
    res = await db()[SESSIONS].delete_one({"_id": _doc_id(platform, session_id)})
    return res.deleted_count > 0


async def delete_pool(platform: str) -> int:
    res = await db()[SESSIONS].delete_many({"platform": platform})
    return res.deleted_count


# ---------- health cache ----------

async def item_last_checked(platform: str, session_id: str) -> Optional[datetime]:
    doc = await db()[SESSION_ITEM_HEALTH].find_one({"platform": platform, "session_id": session_id})
    return doc.get("checked_at") if doc else None


async def record_item_health(platform: str, session_id: str, identifier: str, ok: bool, detail: str) -> Optional[dict]:
    """Returns the PREVIOUS doc (or None if first check) so the caller can
    decide whether this is a fresh failure worth an incident."""
    coll = db()[SESSION_ITEM_HEALTH]
    previous = await coll.find_one({"platform": platform, "session_id": session_id})
    await coll.update_one(
        {"platform": platform, "session_id": session_id},
        {"$set": {
            "platform": platform, "session_id": session_id, "identifier": identifier,
            "ok": ok, "detail": detail, "checked_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return previous


async def record_platform_health(platform: str, ok: bool, detail: str) -> None:
    await db()[SESSION_HEALTH].update_one(
        {"platform": platform},
        {"$set": {"platform": platform, "ok": ok, "detail": detail, "checked_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def cached_health() -> dict[str, dict]:
    out: dict[str, dict] = {}
    async for d in db()[SESSION_HEALTH].find({}):
        out[d["platform"]] = {"ok": d.get("ok", True), "detail": d.get("detail", ""), "checked_at": d.get("checked_at")}
    return out


async def ensure_indexes() -> None:
    await db()[SESSIONS].create_index([("platform", 1), ("session_id", 1)], unique=True, name="uniq_platform_session")
