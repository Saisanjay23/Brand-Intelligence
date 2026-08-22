"""Client business logic, thin today (mostly a repository passthrough),
kept as its own service so controllers never call a repository directly,
per the layering rule (controller -> service -> repository).
"""

from __future__ import annotations

from typing import Optional

from backend.database.repositories import client_repository as clients_db
from backend.database.repositories import evidence_repository as evidence_db
from backend.database.repositories import incident_repository as incidents_db
from backend.database.repositories import profile_repository as profiles_db
from backend.database.repositories import published_incident_repository as published_incidents_db


async def upsert(
    client_id: str, name: str, domain: str = "",
    name_keywords: Optional[list[str]] = None,
    domain_keywords: Optional[list[str]] = None,
    # See client_repository.upsert: `None` rather than a shared mutable default.
    asset_name_individual_keywords: Optional[list[str]] = None,
    asset_name_domain_keywords: Optional[list[str]] = None,
    platform_limits_individual: Optional[dict[str, int]] = None,
    platform_limits_domain: Optional[dict[str, int]] = None,
    platform_tab_limits: Optional[dict[str, dict[str, object]]] = None,
    cron: Optional[str] = None,
    # Parent/child keyword groups. When supplied this is AUTHORITATIVE and
    # the flat name_keywords/domain_keywords above are re-derived from its
    # parents (see client_repository.upsert), so a caller cannot save the
    # two in a state where they disagree.
    keyword_groups: Optional[dict] = None,
) -> dict:
    return await clients_db.upsert(
        client_id, name, domain, name_keywords, domain_keywords,
        platform_limits_individual, platform_limits_domain, platform_tab_limits, cron,
        asset_name_individual_keywords, asset_name_domain_keywords,
        keyword_groups,
    )


async def get(client_id: str) -> dict:
    return await clients_db.get(client_id)


async def add_keyword(client_id: str, keyword: str, kind: str) -> None:
    keyword = keyword.strip()
    if keyword:
        await clients_db.add_keyword(client_id, keyword, kind)


async def list_all() -> list[dict]:
    return await clients_db.list_all()


async def reorder(client_ids: list[str]) -> list[dict]:
    await clients_db.reorder(client_ids)
    return await clients_db.list_all()


async def delete(client_id: str) -> dict:
    """Deleting a client cascades: every profile, every client-scoped
    incident, every published incident, and every GridFS evidence
    screenshot for it is removed too, not just the client record itself.
    The evidence cleanup used to be missing entirely, a screenshot's only
    link to a client is its `{client_id}/{platform}/...` filename prefix,
    not a field a plain `profiles` collection delete would ever reach, so
    every client deletion left every screenshot it ever captured behind in
    GridFS forever."""
    deleted_profiles = await profiles_db.delete_for_client(client_id)
    deleted_incidents = await incidents_db.delete_for_client(client_id)
    deleted_published = await published_incidents_db.delete_for_client(client_id)
    deleted_evidence = await evidence_db.delete_for_client(client_id)
    out = await clients_db.delete(client_id)
    return {
        **out, "deleted_profiles": deleted_profiles, "deleted_incidents": deleted_incidents,
        "deleted_published_incidents": deleted_published, "deleted_evidence": deleted_evidence,
    }
