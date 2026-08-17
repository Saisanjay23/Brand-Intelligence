"""Read-only findings feed, the durable record `POST /profiles/{id}/publish`
writes, distinct from the internal job-failure diagnostic log kept in
`services/incident_service.py` (that log's own read endpoint was removed
along with the Dashboard page, its only reader; the write path is untouched
and still records every job failure)."""

from __future__ import annotations

from typing import Optional

from backend.services import incident_publisher


async def list_published(client_id: str, platform: Optional[str] = None, limit: int = 50, offset: int = 0) -> dict:
    return await incident_publisher.list_published(client_id, platform, limit, offset)
