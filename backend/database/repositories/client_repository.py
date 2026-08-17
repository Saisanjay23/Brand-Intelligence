"""Client persistence, the `clients` collection, one document per
caller-supplied `client_id` (your SaaS's own customer/org id, passed
straight through and used as-is, never regenerated).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.shared.errors import NotFoundError
from backend.database.connection import db

CLIENTS = "clients"


def _utc(v):
    """Motor hands every datetime back naive-but-UTC-VALUED; stamp UTC
    explicitly on the way out so a JSON client doesn't read the unmarked
    ISO string as local time (same fix as profile_repository's
    `_stamp_utc_for_api`), otherwise the Scheduler tab's exact last-run
    timestamp would be off by the browser's own UTC offset."""
    return v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and v.tzinfo is None else v


def _to_out(doc: dict) -> dict:
    return {
        "client_id": doc["_id"],
        "name": doc.get("name", ""),
        "domain": doc.get("domain", ""),
        # two deliberately separate curated lists, not one merged bag:
        # individual names (people to protect) and domain/brand keyword
        # variants are different kinds of search terms an analyst tunes
        # independently. Combined at search time, never merged in storage.
        "name_keywords": doc.get("name_keywords", []),
        "domain_keywords": doc.get("domain_keywords", []),
        "asset_name_individual_keywords": doc.get("asset_name_individual_keywords", []),
        "asset_name_domain_keywords": doc.get("asset_name_domain_keywords", []),
        # per-platform discovery cap, keyed by platform id, scoped
        # separately to individual-keyword vs domain-keyword sweeps, a
        # platform absent from either map (or mapped to 0) means "scrape
        # everything" for that keyword type. See services/discovery_service.py,
        # which reads these per platform when a sweep starts.
        #
        # Falls back to the pre-split `platform_limits` field for a document
        # saved before this existed, applied to BOTH keyword types (the
        # closest match to what a single combined cap used to mean)
        # never silently uncapped just because the client record predates
        # this field. The next save through client_service.upsert writes
        # real individual/domain values and drops the legacy field for good.
        "platform_limits_individual": doc.get("platform_limits_individual") or doc.get("platform_limits") or {},
        "platform_limits_domain": doc.get("platform_limits_domain") or doc.get("platform_limits") or {},
        # platform id -> {tab -> {"individual"/"domain": cap}}, for
        # platforms with more than one discovery tab, currently only
        # Facebook (people/pages/groups). A document saved before this was
        # split by keyword type may still carry the legacy flat {tab: cap}
        # shape, discovery_service.py's cap lookup understands both.
        "platform_tab_limits": doc.get("platform_tab_limits", {}),
        "cron": doc.get("cron"),
        "created_at": _utc(doc.get("created_at")),
        # set by the round-robin engine after each of its turns for this
        # client, see services/round_robin_service.py::_process_client.
        # Absent entirely for a client the engine hasn't reached yet.
        "last_run_at": _utc(doc.get("last_run_at")),
        "last_run_status": doc.get("last_run_status"),
        "last_run_note": doc.get("last_run_note", ""),
        # wall-clock seconds the most recent completed turn took (discovery
        # + any analysis catch-up combined). None for a client that
        # hasn't completed a turn yet, or one saved before this field
        # existed.
        "last_run_duration_s": doc.get("last_run_duration_s"),
        # total completed turns since this client was created, success,
        # failed, and skipped alike, since all three mean the round-robin
        # engine actually reached this client's slot in the rotation.
        "run_count": doc.get("run_count", 0),
    }


async def upsert(
    client_id: str, name: str, domain: str = "",
    name_keywords: Optional[list[str]] = None, domain_keywords: Optional[list[str]] = None,
    platform_limits_individual: Optional[dict[str, int]] = None,
    platform_limits_domain: Optional[dict[str, int]] = None,
    platform_tab_limits: Optional[dict[str, dict[str, object]]] = None,
    cron: Optional[str] = None,
    asset_name_individual_keywords: list[str] = [],
    asset_name_domain_keywords: list[str] = [],
) -> dict:
    """`cron` is optional, a client with keywords but no cron only ever
    gets swept when `POST /discovery` is called for it explicitly; setting
    cron additionally schedules an automatic recurring sweep (see
    sessions/manager.py / services/scheduler_service.py)."""
    now = datetime.now(timezone.utc)
    name_kw = name_keywords or []
    domain_kw = domain_keywords or []
    await db()[CLIENTS].update_one(
        {"_id": client_id},
        {
            "$set": {
                "name": name, "domain": domain,
                "name_keywords": name_kw, "domain_keywords": domain_kw,
                "platform_limits_individual": platform_limits_individual or {},
                "platform_limits_domain": platform_limits_domain or {},
                "platform_tab_limits": platform_tab_limits or {},
                "cron": cron,
                "asset_name_individual_keywords": asset_name_individual_keywords or [],
                "asset_name_domain_keywords": asset_name_domain_keywords or [],
            },
            # legacy pre-split field, if any, is superseded the moment this
            # client is saved through the current form, _to_out's own
            # fallback only ever needs to cover a document nobody has
            # resaved since the split, never one that just went through here.
            "$unset": {"platform_limits": ""},
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
    """Like `get`, but returns None instead of raising, for internal
    engine code that needs to check existence without a 404 semantics."""
    doc = await db()[CLIENTS].find_one({"_id": client_id})
    return _to_out(doc) if doc else None


async def list_all() -> list[dict]:
    """Every client, used by the scheduler's cron sync and the analysis
    catch-up sweep, which operate across all of them."""
    return [_to_out(d) async for d in db()[CLIENTS].find({})]


async def record_run_result(
    client_id: str, status: str, note: str = "", duration_s: Optional[float] = None,
) -> None:
    """Called by the round-robin engine after every turn it takes on this
    client, feeds the Scheduler admin tab's last-run/status/duration
    columns and its running total. `status` is "success" | "failed" |
    "skipped". A plain `update_one`, not an upsert: the round-robin engine
    only ever processes clients that already exist."""
    fields: dict = {
        "last_run_at": datetime.now(timezone.utc),
        "last_run_status": status,
        "last_run_note": note,
    }
    if duration_s is not None:
        fields["last_run_duration_s"] = round(duration_s, 1)
    await db()[CLIENTS].update_one(
        {"_id": client_id},
        {"$set": fields, "$inc": {"run_count": 1}},
    )


async def delete(client_id: str) -> dict:
    doc = await db()[CLIENTS].find_one_and_delete({"_id": client_id})
    if doc is None:
        raise NotFoundError(f"client {client_id!r} not found")
    return _to_out(doc)


async def ensure_indexes() -> None:
    pass  # _id is already the unique key; nothing extra to index here
