"""Profile persistence -- the `profiles` collection, one document per
`(client_id, platform, url)`. Every platform's discovery/analysis results
land in the same collection, distinguished by a `platform` field, so
"every profile for this client" is one query, not a fan-out across
per-platform databases (see docs/adr/0004 -- the reasoning for a single
shared document per profile is unchanged from the original design, just
the physical storage got simpler).

Writes are field-scoped on purpose: discovery must never blank the analysis
fields of a profile it rediscovers, and an analyst's approve/reject must
never be undone by the next sweep. This is the single most important
invariant in the whole engine -- it's what makes a daily re-sweep safe to
run unattended.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from backend.shared.errors import NotFoundError, ValidationError
from backend.shared.logging import get_logger
from backend.shared.mongo import db

log = get_logger("db.profiles")

PROFILES = "profiles"

PHASE_DISCOVERY = "discovery"
PHASE_ANALYSIS = "analysis"

# fields discovery is allowed to write; everything else is analysis territory
DISCOVERY_FIELDS = (
    "entity_id", "username", "display_name", "entity_type",
    "discovery_source", "profile_image_url", "has_logo",
)

ANALYSIS_FIELDS = (
    "entity_id", "display_name", "entity_type", "target", "official_feed",
    "followers", "followers_exact", "friends", "location", "profile_image_url",
    "has_logo", "is_active", "has_name_match", "name_score", "last_post_date",
    "risk_score", "priority", "comments", "analysis_status", "sources",
)

# what an analyst may correct by hand -- a whitelist, so a stray PATCH cannot
# rewrite scraped evidence unlabelled
EDITABLE = {
    "has_logo", "is_active", "has_name_match", "risk_score", "priority",
    "comments", "target", "official_feed", "status",
    "display_name", "followers", "location", "last_post_date",
}

# fields a rediscovery can actually observe that matter for reconsidering a
# REJECTED decision -- name or photo, the two things that would make an
# analyst want another look at something they already dismissed
RECONSIDER_FIELDS = ("display_name", "has_logo", "profile_image_url")

# each of these carries a `sources.<key>` provenance tag under a DIFFERENT
# key than the document field -- a manual edit must relabel the matching key
PROVENANCE_KEYS = {
    "display_name": "name", "followers": "followers", "location": "location",
    "has_logo": "logo", "last_post_date": "last_post",
}

# a manual edit to any of these changes what the risk score/priority ought
# to be, so `patch` must recompute both, not just store the raw value
SCORING_FIELDS = {"has_logo", "has_name_match", "is_active", "followers"}


def _stamp_utc_for_api(doc: dict) -> dict:
    """Mongo hands every datetime back naive-but-UTC-VALUED (motor isn't
    tz_aware); stamp UTC explicitly on the way out so a JSON client doesn't
    read the unmarked ISO string as local time."""
    for f in ("first_seen", "last_seen", "changed_at"):
        v = doc.get(f)
        if isinstance(v, datetime) and v.tzinfo is None:
            doc[f] = v.replace(tzinfo=timezone.utc)
    return doc


def _oid(doc_id: str) -> ObjectId:
    try:
        return ObjectId(doc_id)
    except (InvalidId, TypeError):
        raise NotFoundError(f"profile {doc_id!r} not found")


async def save(
    client_id: str, platform: str, phase: str, fields: dict,
    *, url: str, entity_id: str = "", keyword: str = "",
) -> bool:
    """Upsert one profile. Returns True when newly seen.

    Deduplication is by identity, not URL string -- the platform's own id
    wins when available, the URL is the fallback for entities whose id
    could not be resolved. Every URL a profile has been seen at is kept in
    `urls`. Only the fields the calling phase owns (DISCOVERY_FIELDS /
    ANALYSIS_FIELDS) are ever written, so a discovery sweep re-finding an
    already-scored profile never blanks its score, and neither phase ever
    touches the analyst's `status`.
    """
    coll = db()[PROFILES]
    eid = (entity_id or "").strip()

    match: dict[str, Any] = {"client_id": client_id, "platform": platform}
    keys: list[dict] = []
    if eid:
        keys.append({"entity_id": eid})
    keys.append({"url": url})
    keys.append({"urls": url})
    existing = await coll.find_one(
        {**match, "$or": keys},
        {"_id": 1, "url": 1, "status": 1, **{f: 1 for f in RECONSIDER_FIELDS}},
    )

    owned = ANALYSIS_FIELDS if phase == PHASE_ANALYSIS else DISCOVERY_FIELDS
    update: dict[str, Any] = {
        "$set": {k: v for k, v in fields.items() if k in owned and v not in (None, "", {})},
        "$currentDate": {"last_seen": True},
        "$addToSet": {"urls": url},
    }
    # phase only ever advances -- a sweep that rediscovers an
    # already-scored profile must not demote it back to "discovery"
    if phase == PHASE_ANALYSIS:
        update["$set"]["phase"] = PHASE_ANALYSIS
    if keyword:
        update["$addToSet"]["keywords"] = keyword

    if existing:
        if existing.get("status") == "rejected":
            changed = {
                f: {"old": existing.get(f), "new": fields[f]}
                for f in RECONSIDER_FIELDS
                if fields.get(f) not in (None, "", {}) and fields.get(f) != existing.get(f)
            }
            if changed:
                update["$set"]["status"] = "pending"
                update["$set"]["changes"] = changed
                update["$set"]["changed_at"] = datetime.now(timezone.utc)
                log.info(
                    f"{platform}/{client_id}: {url} rejected profile changed "
                    f"({', '.join(changed)}) -- back to pending"
                )
        await coll.update_one({"_id": existing["_id"]}, update)
        return False

    update["$setOnInsert"] = {
        "client_id": client_id, "platform": platform, "url": url,
        "entity_id": eid, "status": "pending",
        "first_seen": datetime.now(timezone.utc),
    }
    if phase != PHASE_ANALYSIS:
        update["$setOnInsert"]["phase"] = phase
    try:
        res = await coll.update_one({**match, "url": url}, update, upsert=True)
        return res.upserted_id is not None
    except DuplicateKeyError:
        # another writer inserted the same profile between the read and
        # the write; treat it as already seen rather than failing the batch
        await coll.update_one({**match, "url": url}, update)
        return False


async def save_many(
    client_id: str, platform: str, phase: str, items: list[dict],
) -> tuple[int, int]:
    """Each item is `{**fields, "url":..., "entity_id":..., "keyword":...}`.
    -> (saved, newly seen). One bad row never sinks the batch."""
    saved = new = 0
    for item in items:
        item = dict(item)
        url = item.pop("url")
        entity_id = item.pop("entity_id", "")
        keyword = item.pop("keyword", "")
        try:
            if await save(client_id, platform, phase, item, url=url, entity_id=entity_id, keyword=keyword):
                new += 1
            saved += 1
        except Exception as e:
            log.warning(f"save failed for {url}: {type(e).__name__}: {e}")
    return saved, new


async def find(
    client_id: str, *, platform: Optional[str] = None, status: Optional[str] = None,
    phase: Optional[str] = None, limit: int = 100, offset: int = 0,
) -> tuple[list[dict], int]:
    q: dict[str, Any] = {"client_id": client_id}
    if platform:
        q["platform"] = platform
    if status:
        q["status"] = status
    if phase:
        if phase == PHASE_DISCOVERY:
            q["$or"] = [{"phase": PHASE_DISCOVERY}, {"status": "approved"}]
        else:
            q["phase"] = phase

    coll = db()[PROFILES]
    total = await coll.count_documents(q)
    rows = []
    async for doc in coll.find(q).sort("last_seen", -1).skip(offset).limit(limit):
        doc["id"] = str(doc.pop("_id"))
        rows.append(_stamp_utc_for_api(doc))
    return rows, total


async def urls_for(
    client_id: str, platform: str, status: Optional[str] = None,
    *, exclude_analysed: bool = False,
) -> list[str]:
    q: dict[str, Any] = {"client_id": client_id, "platform": platform}
    if status:
        q["status"] = status
    if exclude_analysed:
        q["phase"] = {"$ne": PHASE_ANALYSIS}
    return [d["url"] async for d in db()[PROFILES].find(q, {"url": 1, "_id": 0}) if d.get("url")]


async def get_by_id(doc_id: str) -> Optional[dict]:
    doc = await db()[PROFILES].find_one({"_id": _oid(doc_id)})
    if doc is None:
        return None
    doc["id"] = str(doc.pop("_id"))
    return doc


def compute_risk_score(has_logo: bool, has_name_match: bool, is_active: bool, followers) -> int:
    """The same rubric used during a live scrape (`engine/scoring.py`'s
    `Row.risk`), applied to a document's already-derived boolean fields --
    so a hand correction and a fresh scrape can never silently disagree
    about how the same facts turn into a score."""
    from backend.engine.scoring import BASE, REACH_AT, W_ACTIVE, W_LOGO, W_NAME, W_REACH

    score = BASE
    if has_logo:
        score += W_LOGO
    if has_name_match:
        score += W_NAME
    if is_active:
        score += W_ACTIVE
    if (followers or 0) >= REACH_AT:
        score += W_REACH
    return score


def compute_priority(has_logo: bool, has_name_match: bool) -> str:
    if has_logo:
        return "High"
    return "Medium" if has_name_match else "Low"


async def patch(doc_id: str, fields: dict) -> dict:
    """Applies an analyst's whitelisted edit, recomputes score/priority
    when a scoring-relevant field changed, and relabels provenance so a
    manual correction never masquerades as scraped evidence."""
    safe = {k: v for k, v in fields.items() if k in EDITABLE}
    if "followers" in safe:
        try:
            safe["followers"] = None if safe["followers"] in (None, "") else int(safe["followers"])
        except (TypeError, ValueError):
            raise ValidationError("followers must be a number")
    if not safe:
        raise ValidationError("nothing updatable in that payload")

    oid = _oid(doc_id)
    if SCORING_FIELDS & safe.keys():
        doc = await db()[PROFILES].find_one({"_id": oid})
        if doc is None:
            raise NotFoundError(f"profile {doc_id!r} not found")
        merged = {**doc, **safe}
        safe["risk_score"] = compute_risk_score(
            merged.get("has_logo", False), merged.get("has_name_match", False),
            merged.get("is_active", False), merged.get("followers"),
        )
        safe["priority"] = compute_priority(merged.get("has_logo", False), merged.get("has_name_match", False))

    for field_name, source_key in PROVENANCE_KEYS.items():
        if field_name in safe:
            safe[f"sources.{source_key}"] = "manual"
    safe["last_seen"] = datetime.now(timezone.utc)

    write: dict[str, Any] = {"$set": safe}
    if "status" in safe:
        # a fresh decision resolves whatever reconsideration flagged this
        # profile -- the "changed since rejection" label must not outlive it
        write["$unset"] = {"changes": "", "changed_at": ""}

    res = await db()[PROFILES].update_one({"_id": oid}, write)
    if res.matched_count == 0:
        raise NotFoundError(f"profile {doc_id!r} not found")
    updated = await get_by_id(doc_id)
    return updated or {}


async def stats(client_id: str, platform: Optional[str] = None) -> dict:
    import asyncio

    coll = db()[PROFILES]
    base: dict[str, Any] = {"client_id": client_id}
    if platform:
        base["platform"] = platform
    keys = (
        ("total", base),
        ("pending", {**base, "status": "pending"}),
        ("approved", {**base, "status": "approved"}),
        ("rejected", {**base, "status": "rejected"}),
        ("high", {**base, "priority": "High"}),
        ("medium", {**base, "priority": "Medium"}),
        ("low", {**base, "priority": "Low"}),
        ("analysed", {**base, "phase": PHASE_ANALYSIS}),
    )
    counts = await asyncio.gather(*(coll.count_documents(f) for _, f in keys))
    return dict(zip((k for k, _ in keys), counts))


async def delete_for_client(client_id: str) -> int:
    res = await db()[PROFILES].delete_many({"client_id": client_id})
    return res.deleted_count


async def ensure_indexes() -> None:
    coll = db()[PROFILES]
    await coll.create_index(
        [("client_id", 1), ("platform", 1), ("url", 1)], unique=True, name="uniq_client_platform_url"
    )
    await coll.create_index(
        [("client_id", 1), ("platform", 1), ("entity_id", 1)],
        unique=True, name="uniq_client_platform_entity",
        partialFilterExpression={"entity_id": {"$type": "string", "$gt": ""}},
    )
    await coll.create_index([("client_id", 1), ("platform", 1), ("urls", 1)], name="client_platform_urls")
    await coll.create_index([("client_id", 1), ("status", 1), ("last_seen", -1)], name="client_status_seen")
    await coll.create_index([("client_id", 1), ("priority", 1)], name="client_priority")
    await coll.create_index([("client_id", 1), ("keywords", 1)], name="client_keywords")
