"""Liveness/readiness for orchestration (k8s-style probes), a per-platform
health snapshot for an ops dashboard, and Prometheus scrape target.
Deliberately public (no auth), these carry no client data.
"""

from __future__ import annotations

from backend.services import health_service as health_engine
from backend.platforms import registry
from backend.shared.metrics import render_latest
from backend.database.connection import db, ping

# every collection this app writes to, what an operator actually wants to
# see when sizing an Atlas tier or deciding whether cleanup_stale_pending /
# archive_stale_rejected (profile_repository.py) are worth running.
_OWNED_COLLECTIONS = ("profiles", "clients", "sessions", "incidents", "published_incidents")


async def live() -> dict:
    return {"status": "ok"}


async def ready() -> tuple[dict, bool]:
    ok = await ping()
    return {"status": "ok" if ok else "unavailable", "mongo": ok}, ok


async def platforms() -> dict:
    out = []
    for platform_id, plat in registry.PLATFORMS.items():
        out.append({
            "platform": platform_id, "name": plat.name, "enabled": plat.enabled,
            "session_state": await registry.session_state(plat),
            # surfaced to the frontend so an analyst knows if a platform has any
            # standing stability caveat before running analysis.
            "stability_note": plat.stability_note,
            "analysis_stub": plat.analysis_stub,
            **health_engine.one(platform_id),
        })
    return {"items": out}


async def metrics() -> tuple[bytes, str]:
    return render_latest()


async def data_integrity() -> dict:
    """Documents that violate what the unique indexes are supposed to
    guarantee. `ensure_indexes` no longer refuses to start the app when it
    can't build one (see profile_repository), this is where the answer to
    "why not, and which documents" lives."""
    from backend.database.repositories import profile_repository as profiles_db

    duplicates = await profiles_db.find_duplicate_identities()
    return {
        "ok": not duplicates,
        "duplicate_profiles": duplicates,
        "hint": (
            "Each group lists documents sharing an identity that must be unique. "
            "Merge or delete all but one per group (keep the one with an analyst "
            "decision and/or analysis results), then restart to let the unique "
            "index build."
        ) if duplicates else "",
    }


async def collection_sizes() -> dict:
    """Real on-disk/index size per collection, straight from Mongo's own
    collStats, so sizing an Atlas tier (or checking whether the profile
    cleanup/archive maintenance calls are actually moving the needle)
    doesn't require guessing from document counts alone."""
    out: dict[str, dict] = {}
    for name in _OWNED_COLLECTIONS:
        try:
            s = await db().command("collStats", name)
        except Exception:
            out[name] = {"error": "collection missing or inaccessible"}
            continue
        out[name] = {
            "docs": s.get("count", 0),
            "data_mb": round(s.get("size", 0) / 1024 / 1024, 2),
            "index_mb": round(s.get("totalIndexSize", 0) / 1024 / 1024, 2),
            "total_mb": round((s.get("size", 0) + s.get("totalIndexSize", 0)) / 1024 / 1024, 2),
        }
    return out
