"""`POST /discovery` sweeps a client's keywords across either every ready
platform, or one specific platform when the caller names it (see
`dto/discovery_dto.py`'s `platform` field, the Sweep button's "All
Platforms" vs. one-platform selector). The client itself is
created/configured separately via `POST /clients`, this no longer
upserts it as a side effect, since a client's org id/name/domain/keyword
config is now curated up front, not inferred from whatever a discovery
call happened to be called with.
"""

from __future__ import annotations

from backend.dto.discovery_dto import DiscoveryIn
from backend.services import client_service
from backend.services.job_service import DISCOVERY, job_manager
from backend.services.webhook_service import validate_callback_url
from backend.shared.errors import ValidationError


def _validated_platform(platform_id: str) -> str:
    """Raises ValidationError for anything that isn't a real,
    discovery-capable platform id, rather than letting a typo silently
    become an "all platforms" sweep (blank) or a job that starts, finds
    nothing to do, and gives no clue why."""
    from backend.platforms import registry

    plat = registry.PLATFORMS.get(platform_id)
    if plat is None:
        raise ValidationError(f"unknown platform {platform_id!r} -- known: {', '.join(registry.PLATFORMS)}")
    if not plat.can_discover:
        raise ValidationError(f"{platform_id!r} has no discovery phase")
    return platform_id


def _resolve_platforms(
    platforms: "list[str] | None", platform: "str | None",
) -> tuple["str | None", "list[str] | None"]:
    """-> (job.platform, params["platforms"]).

    `job.platform` decides which locks the job takes (see
    job_service::_lock_keys), so it can only ever name ONE platform. A
    multi-platform run therefore reports itself as platform=None -- taking
    every platform's lock, exactly as an "All Platforms" sweep does -- and
    carries the actual selection in params, which is what scopes the
    sweep. Broader locking than strictly needed is safe (it only reduces
    concurrency); narrower would let two jobs drive one session at once.

    A single-item selection collapses to the plain single-platform case so
    it keeps that path's tighter locking and its coalescing behaviour.
    """
    chosen = [p for p in (platforms or []) if str(p).strip()]
    if not chosen:
        return (_validated_platform(platform) if platform else None), None

    seen: list[str] = []
    for pid in chosen:
        valid = _validated_platform(pid)
        if valid not in seen:
            seen.append(valid)
    if len(seen) == 1:
        return seen[0], None
    return None, seen


async def start_discovery(body: DiscoveryIn) -> dict:
    await client_service.get(body.client_id)  # 404s if the client was never configured

    # Reject an unusable callback here rather than after a 40-minute sweep:
    # the caller finds out at request time, and a URL pointing somewhere it
    # must not (an internal service, a cloud metadata endpoint) never gets
    # as far as being fetched. See webhook_service for the full reasoning.
    ok, reason = validate_callback_url(body.callback_url)
    if not ok:
        raise ValidationError(reason)

    if body.profile_ids:
        # a targeted re-resolve of already-discovered profiles, not a fresh
        # keyword search, see discovery_service.py's _resweep_selected.
        # Scoped to "facebook" so it shares that platform's job lock with
        # any concurrent full sweep/analysis run rather than racing the
        # same browser session.
        job = job_manager.create(
            DISCOVERY, body.client_id, {"profile_ids": body.profile_ids},
            platform="facebook", callback_url=body.callback_url,
        )
        return {"job_id": job.id, "status": job.status}

    if not body.keywords:
        raise ValidationError("keywords required (or pass profile_ids to re-resolve specific profiles instead)")

    platform, scoped = _resolve_platforms(body.platforms, body.platform)

    params: dict = {"keywords": body.keywords, "tabs": body.tabs, "max_results": body.max_results}
    if scoped:
        params["platforms"] = scoped
    if body.max_seconds is not None:
        params["max_seconds"] = body.max_seconds
    if body.concurrency is not None:
        params["concurrency"] = body.concurrency

    job = job_manager.create(DISCOVERY, body.client_id, params, platform=platform, callback_url=body.callback_url)
    return {"job_id": job.id, "status": job.status}
