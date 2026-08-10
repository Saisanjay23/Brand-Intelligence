"""Client business logic -- thin today (mostly a repository passthrough),
kept as its own service so controllers never call a repository directly,
per the layering rule (controller -> service -> repository).
"""

from __future__ import annotations

from typing import Optional

from backend.database.repositories import client_repository as clients_db
from backend.database.repositories import incident_repository as incidents_db
from backend.database.repositories import profile_repository as profiles_db
from backend.database.repositories import published_incident_repository as published_incidents_db


async def upsert(
    client_id: str, name: str, domain: str = "",
    name_keywords: Optional[list[str]] = None,
    domain_keywords: Optional[list[str]] = None,
    logo_url: str = "",
    asset_name_individual_keywords: list[str] = [],
    asset_name_domain_keywords: list[str] = [],
    platform_limits: Optional[dict[str, int]] = None,
    platform_tab_limits: Optional[dict[str, dict[str, int]]] = None,
    cron: Optional[str] = None,
    official_handles: Optional[dict[str, str]] = None,
) -> dict:
    return await clients_db.upsert(
        client_id, name, domain, name_keywords, domain_keywords,
        platform_limits, platform_tab_limits, cron,
        logo_url, asset_name_individual_keywords, asset_name_domain_keywords,
        official_handles,
    )


async def get(client_id: str) -> dict:
    return await clients_db.get(client_id)


async def list_all() -> list[dict]:
    return await clients_db.list_all()


async def delete(client_id: str) -> dict:
    """Deleting a client cascades: every profile, every client-scoped
    incident, and every published incident for it is removed too, not
    just the client record itself."""
    deleted_profiles = await profiles_db.delete_for_client(client_id)
    deleted_incidents = await incidents_db.delete_for_client(client_id)
    deleted_published = await published_incidents_db.delete_for_client(client_id)
    out = await clients_db.delete(client_id)
    return {
        **out, "deleted_profiles": deleted_profiles, "deleted_incidents": deleted_incidents,
        "deleted_published_incidents": deleted_published,
    }
