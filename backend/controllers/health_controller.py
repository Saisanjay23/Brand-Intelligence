"""Liveness/readiness for orchestration (k8s-style probes), a per-platform
health snapshot for an ops dashboard, and Prometheus scrape target.
Deliberately public (no auth) -- these carry no client data.
"""

from __future__ import annotations

from backend.services import health_service as health_engine
from backend.services import session_service as sessions_engine
from backend.platforms import registry
from backend.utils.metrics import render_latest
from backend.config.database import ping


async def live() -> dict:
    return {"status": "ok"}


async def ready() -> tuple[dict, bool]:
    ok = await ping()
    return {"status": "ok" if ok else "unavailable", "mongo": ok}, ok


async def platforms() -> dict:
    live_health = await sessions_engine.cached_health()
    out = []
    for platform_id, plat in registry.PLATFORMS.items():
        out.append({
            "platform": platform_id, "name": plat.name, "enabled": plat.enabled,
            "session_state": await registry.session_state(plat),
            **health_engine.one(platform_id),
        })
    return {"items": out}


async def metrics() -> tuple[bytes, str]:
    return render_latest()
