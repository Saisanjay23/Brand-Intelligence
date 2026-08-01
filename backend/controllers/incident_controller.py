"""Read-only incident feed -- see services/incident_service.py for the
diagnosis rulebook."""

from __future__ import annotations

from typing import Optional

from backend.services import incident_service as incidents_engine


async def list_incidents(limit: int = 50, platform: Optional[str] = None) -> dict:
    return {"items": await incidents_engine.recent(limit, platform)}
