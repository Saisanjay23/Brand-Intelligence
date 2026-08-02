"""Profile business logic -- extracted from the old `routes_profiles.py`
handler bodies so the controller layer stays request-in/response-out only.

Two response shapes off the same collection, picked by `phase`:
`phase` omitted/`discovery` -> the triage/card list an analyst reviews
and a UI renders as cards (profile_name + logo, nothing else).
`phase=analysis` -> the final result: client + keyword context inlined
onto each validated, scraped profile (a client has one name, fetched
once per request rather than stored redundantly on every profile).

Approving a profile here is also where analysis gets queued -- the only
place in this layer that reaches into the job service directly, since
that's exactly the "approve -> auto-launch analysis for that profile's
own platform" behavior described in the plan.
"""

from __future__ import annotations

from typing import Optional

from backend.database.repositories import client_repository as clients_db
from backend.database.repositories import profile_repository as profiles_db
from backend.services.job_service import ANALYSIS, job_manager
from backend.shared.errors import NotFoundError
from backend.validators.profile_validator import validate_patch_fields


def _to_card(doc: dict) -> dict:
    return {
        "id": doc["id"], "platform": doc.get("platform", ""), "url": doc.get("url", ""),
        "profile_name": doc.get("display_name") or doc.get("username") or "",
        "profile_image_url": doc.get("profile_image_url", ""),
        "has_logo": bool(doc.get("has_logo", False)),
        "verified": bool(doc.get("verified", False)),
        "status": doc.get("status", "pending"),
        "phase": doc.get("phase", "discovery"),
        "risk_score": doc.get("risk_score"),
        "priority": doc.get("priority"),
        "comments": doc.get("comments"),
        "followers": doc.get("followers"),
    }


def _to_full(doc: dict, client_name: str) -> dict:
    return {
        "id": doc["id"], "client_id": doc.get("client_id", ""), "client_name": client_name,
        "keyword": ", ".join(doc.get("keywords", [])),
        "platform": doc.get("platform", ""), "url": doc.get("url", ""),
        "username": doc.get("username") or doc.get("display_name") or "",
        "profile_name": doc.get("display_name") or doc.get("username") or "",
        "profile_image_url": doc.get("profile_image_url", ""),
        "followers": doc.get("followers"), "location": doc.get("location", ""),
        "last_post_date": doc.get("last_post_date"),
        "has_logo": bool(doc.get("has_logo", False)),
        "verified": bool(doc.get("verified", False)),
        "status": doc.get("status", "pending"),
        "phase": doc.get("phase", "analysis"),
        "risk_score": doc.get("risk_score"),
        "priority": doc.get("priority"),
        "comments": doc.get("comments"),
        "published": doc.get("published", True),
        "publish_hold_until": doc.get("publish_hold_until"),
    }


async def list_profiles(
    client_id: str, status: Optional[str] = None, phase: Optional[str] = None,
    platform: Optional[str] = None, limit: int = 100, offset: int = 0,
    include_held: bool = False,
) -> dict:
    docs, total, counts = await profiles_db.find(
        client_id, platform=platform, status=status, phase=phase, limit=limit, offset=offset,
        include_held=include_held,
    )
    if phase == profiles_db.PHASE_ANALYSIS:
        client = await clients_db.try_get(client_id)
        items = [_to_full(d, client["name"] if client else "") for d in docs]
    else:
        items = [_to_card(d) for d in docs]
    return {"items": items, "total": total, "counts": counts}


async def get_profile(profile_id: str) -> dict:
    doc = await profiles_db.get_by_id(profile_id)
    if doc is None:
        raise NotFoundError(f"profile {profile_id!r} not found")
    client = await clients_db.try_get(doc.get("client_id", ""))
    return _to_full(doc, client["name"] if client else "")


async def patch_profile(profile_id: str, body_fields: dict) -> dict:
    fields = validate_patch_fields({k: v for k, v in body_fields.items() if v is not None})
    updated = await profiles_db.patch(profile_id, fields)
    if fields.get("status") == "approved":
        job_manager.create(ANALYSIS, updated["client_id"], {}, platform=updated.get("platform"))
    return updated


async def publish_profile(profile_id: str) -> dict:
    """An analyst confirming a held analysis result before its hold clears
    on its own -- see ADR 0007."""
    return await profiles_db.publish(profile_id)
