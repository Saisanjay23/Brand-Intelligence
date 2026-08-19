"""Published-incident persistence, the `published_incidents` collection,
one document per source URL, in the exact client-facing shape
`services/incident_publisher.py` builds. Deliberately a different
collection from `incidents` (see incident_repository.py), which is an
unrelated job-failure diagnostic log with a 14-day TTL, a published
incident is a durable finding for a client and is never auto-expired.

Re-publishing an already-published source URL (a rediscovered/re-analysed
profile) only writes the top-level fields whose value actually changed,
an untouched field is left alone rather than rewritten with the same
value, so `last_updated_at` only moves when something genuinely did.

IDENTITY IS (orgId, source), NOT source ALONE. One impersonating account
routinely targets more than one client, a single fake profile lifting
several brands, or sibling orgs managed by the same agency, and each
client owns its own separate finding about it. Keying on the URL alone
meant client B publishing the same profile REWROTE client A's incident in
place, orgId and all: A's finding silently became B's, and A's cascade
delete would then take B's with it. The unique index is compound for the
same reason.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.shared.logging import get_logger
from backend.database.connection import db

log = get_logger("repositories.published_incident_repository")

PUBLISHED_INCIDENTS = "published_incidents"


def _diff(existing: dict, incoming: dict) -> dict:
    return {k: v for k, v in incoming.items() if existing.get(k) != v}


async def upsert(incident: dict) -> dict:
    """Insert a new published incident, or update only the fields that
    changed on one already published for this `(orgId, source)` pair."""
    coll = db()[PUBLISHED_INCIDENTS]
    now = datetime.now(timezone.utc)
    key = {"orgId": incident.get("orgId", ""), "source": incident["source"]}
    existing = await coll.find_one(key)

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


async def find(org_id: str, *, platform: Optional[str] = None, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """A client's published findings, most recently updated first, the
    read side of `upsert`. `platform` filters on the same `assetType` name
    `upsert`'s caller (incident_publisher.build_incident_doc) writes, e.g.
    "Facebook", callers pass a platform id and the service layer resolves
    it to that display name before this ever sees it, since no raw platform
    id is stored on a published-incident document."""
    coll = db()[PUBLISHED_INCIDENTS]
    q: dict = {"orgId": org_id}
    if platform:
        q["assetType"] = platform
    total = await coll.count_documents(q)
    out = []
    async for d in coll.find(q).sort("last_updated_at", -1).skip(offset).limit(limit):
        d["id"] = str(d.pop("_id"))
        out.append(d)
    return out, total


async def delete_for_client(client_id: str) -> int:
    """Part of the client-deletion cascade, see client_service.delete."""
    res = await db()[PUBLISHED_INCIDENTS].delete_many({"orgId": client_id})
    return res.deleted_count


async def delete_for_client_platform(client_id: str, asset_type: str) -> int:
    """Part of the UNSCOPED per-platform delete cascade (phase/status =
    None), see profile_service.delete_for_client_platform. `asset_type` is
    the platform's DISPLAY name (e.g. "Facebook"), not its raw id, the same
    resolution `find`'s own `platform` param above requires, since that's
    what's actually stored on a published-incident document."""
    res = await db()[PUBLISHED_INCIDENTS].delete_many({"orgId": client_id, "assetType": asset_type})
    return res.deleted_count


async def delete_for_sources(client_id: str, sources: list[str]) -> int:
    """The SCOPED per-platform delete cascade: only the published incidents
    for these exact profile URLs, see
    profile_service.delete_for_client_platform. A Discovery-phase delete's
    urls will simply match nothing here (a profile that was never approved
    and analysed was never published either), so this is safe to call
    unconditionally rather than needing its own phase check."""
    if not sources:
        return 0
    res = await db()[PUBLISHED_INCIDENTS].delete_many({"orgId": client_id, "source": {"$in": sources}})
    return res.deleted_count


async def ensure_indexes() -> None:
    coll = db()[PUBLISHED_INCIDENTS]
    # Drop the old URL-only unique index if it is still there: it actively
    # PREVENTS two clients from each holding their own finding about the
    # same impersonating profile, which is the whole bug this replaces.
    try:
        existing = await coll.index_information()
        if "uniq_source" in existing:
            await coll.drop_index("uniq_source")
            log.info("dropped legacy uniq_source index (superseded by uniq_org_source)")
    except Exception as e:
        log.warning(f"could not inspect/drop legacy uniq_source index: {type(e).__name__}: {e}")
    try:
        await coll.create_index([("orgId", 1), ("source", 1)], unique=True, name="uniq_org_source")
    except Exception as e:
        log.error(
            f"could not build unique index 'uniq_org_source': {type(e).__name__}: {e} -- "
            "published_incidents already holds duplicate (orgId, source) pairs; "
            "the diff-only upsert still behaves correctly, it is just no longer enforced."
        )
    await coll.create_index([("orgId", 1)], name="org_id")
