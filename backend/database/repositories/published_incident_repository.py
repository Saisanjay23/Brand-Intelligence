"""Published-incident persistence -- the `published_incidents` collection,
one document per source URL, in the exact client-facing shape
`services/incident_publisher.py` builds. Deliberately a different
collection from `incidents` (see incident_repository.py), which is an
unrelated job-failure diagnostic log with a 14-day TTL -- a published
incident is a durable finding for a client and is never auto-expired.

Re-publishing an already-published source URL (a rediscovered/re-analysed
profile) only writes the top-level fields whose value actually changed --
an untouched field is left alone rather than rewritten with the same
value, so `last_updated_at` only moves when something genuinely did.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.database.connection import db

PUBLISHED_INCIDENTS = "published_incidents"


def _diff(existing: dict, incoming: dict) -> dict:
    return {k: v for k, v in incoming.items() if existing.get(k) != v}


async def upsert(incident: dict) -> dict:
    """Insert a new published incident, or update only the fields that
    changed on one already published for this `source` URL."""
    coll = db()[PUBLISHED_INCIDENTS]
    now = datetime.now(timezone.utc)
    existing = await coll.find_one({"source": incident["source"]})

    if existing is None:
        doc = {**incident, "first_published_at": now, "last_updated_at": now}
        res = await coll.insert_one(doc)
        doc["id"] = str(res.inserted_id)
        doc.pop("_id", None)
        return doc

    changed = _diff(existing, incident)
    if changed:
        changed["last_updated_at"] = now
        await coll.update_one({"_id": existing["_id"]}, {"$set": changed})
        existing.update(changed)
    existing["id"] = str(existing.pop("_id"))
    return existing


async def delete_for_client(client_id: str) -> int:
    """Part of the client-deletion cascade -- see client_service.delete."""
    res = await db()[PUBLISHED_INCIDENTS].delete_many({"orgId": client_id})
    return res.deleted_count


async def ensure_indexes() -> None:
    await db()[PUBLISHED_INCIDENTS].create_index([("source", 1)], unique=True, name="uniq_source")
    await db()[PUBLISHED_INCIDENTS].create_index([("orgId", 1)], name="org_id")
