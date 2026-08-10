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


def _clean_handle(raw: str) -> str:
    """A pasted handle/URL -> the bare handle to store.

    Kept human-readable (case and separators preserved) rather than fully
    normalised: `handle_score` does its own normalisation at compare time,
    and the client-config form has to show the operator something they
    recognise as what they typed.
    """
    s = (raw or "").strip()
    if "/" in s:
        parts = [p for p in s.split("/") if p]
        s = parts[-1] if parts else ""
    return s.split("?")[0].lstrip("@").strip()


def _to_out(doc: dict) -> dict:
    return {
        "client_id": doc["_id"],
        "name": doc.get("name", ""),
        "domain": doc.get("domain", ""),
        "logo_url": doc.get("logo_url", ""),
        # two deliberately separate curated lists, not one merged bag --
        # individual names (people to protect) and domain/brand keyword
        # variants are different kinds of search terms an analyst tunes
        # independently. Combined at search time, never merged in storage.
        "name_keywords": doc.get("name_keywords", []),
        "domain_keywords": doc.get("domain_keywords", []),
        "asset_name_individual_keywords": doc.get("asset_name_individual_keywords", []),
        "asset_name_domain_keywords": doc.get("asset_name_domain_keywords", []),
        # per-platform discovery cap, keyed by platform id -- a platform
        # absent from this map (or mapped to 0) means "scrape everything",
        # never "scrape nothing". See services/discovery_service.py, which
        # reads this per platform when a sweep starts.
        "platform_limits": doc.get("platform_limits", {}),
        # platform id -> {tab: cap}, for platforms with more than one
        # discovery tab -- currently only Facebook (people vs pages).
        "platform_tab_limits": doc.get("platform_tab_limits", {}),
        # platform id -> the brand's own official handle there; see
        # dto/client_dto.py and shared/text.py::handle_score
        "official_handles": doc.get("official_handles", {}),
        "cron": doc.get("cron"),
        "created_at": doc.get("created_at"),
        # set by the round-robin engine after each of its turns for this
        # client -- see services/round_robin_service.py::_process_client.
        # Absent entirely for a client the engine hasn't reached yet.
        "last_run_at": doc.get("last_run_at"),
        "last_run_status": doc.get("last_run_status"),
        "last_run_note": doc.get("last_run_note", ""),
    }


async def upsert(
    client_id: str, name: str, domain: str = "",
    name_keywords: Optional[list[str]] = None, domain_keywords: Optional[list[str]] = None,
    platform_limits: Optional[dict[str, int]] = None,
    platform_tab_limits: Optional[dict[str, dict[str, int]]] = None,
    cron: Optional[str] = None,
    logo_url: str = "",
    asset_name_individual_keywords: list[str] = [],
    asset_name_domain_keywords: list[str] = [],
    official_handles: Optional[dict[str, str]] = None,
) -> dict:
    """`cron` is optional -- a client with keywords but no cron only ever
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
                "name": name, "domain": domain, "logo_url": logo_url,
                "name_keywords": name_kw, "domain_keywords": domain_kw,
                "platform_limits": platform_limits or {},
                "platform_tab_limits": platform_tab_limits or {},
                # normalised on the way in: an operator will paste "@Handle"
                # or a full profile URL as readily as a bare handle, and
                # handle_score() would cope with any of them -- but storing
                # the raw paste means the client-config form redisplays
                # whatever was typed rather than what is actually matched on.
                "official_handles": {
                    k: _clean_handle(v)
                    for k, v in (official_handles or {}).items()
                    if _clean_handle(v)
                },
                "cron": cron,
                "asset_name_individual_keywords": asset_name_individual_keywords or [],
                "asset_name_domain_keywords": asset_name_domain_keywords or [],
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


async def record_run_result(client_id: str, status: str, note: str = "") -> None:
    """Called by the round-robin engine after every turn it takes on this
    client -- feeds the Scheduler admin tab's last-run/status columns.
    `status` is "success" | "failed" | "skipped". A plain `update_one`, not
    an upsert: the round-robin engine only ever processes clients that
    already exist."""
    await db()[CLIENTS].update_one(
        {"_id": client_id},
        {"$set": {
            "last_run_at": datetime.now(timezone.utc),
            "last_run_status": status,
            "last_run_note": note,
        }},
    )


async def delete(client_id: str) -> dict:
    doc = await db()[CLIENTS].find_one_and_delete({"_id": client_id})
    if doc is None:
        raise NotFoundError(f"client {client_id!r} not found")
    return _to_out(doc)


async def ensure_indexes() -> None:
    pass  # _id is already the unique key; nothing extra to index here
