"""Liveness/readiness for orchestration (k8s-style probes), a per-platform
health snapshot for an ops dashboard, and Prometheus scrape target.
Deliberately public (no auth) -- these carry no client data.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from backend.engine import health as health_engine
from backend.engine import sessions as sessions_engine
from backend.platforms import registry
from backend.shared.metrics import render_latest
from backend.shared.mongo import ping

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(response: Response) -> dict:
    ok = await ping()
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "unavailable", "mongo": ok}


@router.get("/health/platforms")
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


@router.get("/metrics")
async def metrics() -> Response:
    body, content_type = render_latest()
    return Response(content=body, media_type=content_type)
