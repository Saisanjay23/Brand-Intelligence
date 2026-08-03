"""Shapes an analysed Profile document into the client-facing
published-incident record and upserts it into `published_incidents`,
keyed by source URL so a rediscovered/re-analysed profile updates its
existing record instead of duplicating it (see
database/repositories/published_incident_repository.py for the
diff-only write).

Deliberately triggered only by an explicit Publish (single "Publish Now"
or the bulk "Publish All"), never by analysis finishing on its own -- the
publish-hold review step (ADR 0007) stays meaningful, and this is exactly
the moment a result actually leaves the tool for a client to see.

`build_incident_doc` is also called to render a live PREVIEW of this same
shape on every analysis-phase GET (see profile_service._to_full), so an
analyst can review/correct it before publishing -- `doc["incident_overrides"]`
holds whatever fields an analyst has hand-edited (flat dotted keys, e.g.
"socialProfileInfo.location"), applied on top of the computed defaults
here, so an override always wins and survives a rediscovery/re-analysis
recomputing everything else.

`created_iso` (account creation date) is captured by some platform
engines' Row but is not one of ANALYSIS_FIELDS persisted on a Profile
document (see profile_repository.py), so "isActive" here can only be
judged from `last_post_date` -- not from creation date as well, simply
because creation date is never stored.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from backend.database.repositories import published_incident_repository as incidents_db
from backend.platforms.registry import PLATFORMS
from backend.shared.models.incident_scoring import compute_incident_risk_score

# "less than 6 months" per the explicit spec for this field, distinct from
# the tool's own internal is_active (90-day window, shared/models/scoring.py)
ACTIVE_WINDOW_DAYS = 183

# category/subCategory branch on whether the profile was found under one
# of the client's individual-name keywords (impersonating a person) or a
# domain/brand keyword (the existing default categorisation)
CATEGORY_PERSON = ("impersonation", "ExecutivePeople")
CATEGORY_BRAND = ("Brand Infringement", "socialhandlers")


def _display_name(doc: dict) -> str:
    return doc.get("display_name") or doc.get("username") or ""


def _asset_type(platform: str) -> str:
    p = PLATFORMS.get(platform)
    return p.name if p else platform.title()


def _is_recent(iso: Optional[str], *, days: int) -> bool:
    if not iso:
        return False
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days < days


def _is_active(doc: dict) -> bool:
    return _is_recent(doc.get("last_post_date"), days=ACTIVE_WINDOW_DAYS)


def _category_and_asset_name(doc: dict, client: dict) -> tuple[str, str, str]:
    """assetName is the client's own name for a domain/brand-keyword match
    (the existing default), but the specific individual-name keyword that
    matched -- not the client's name -- for a person match: "adani" the
    company and "gautam adani" the person are different assets even
    though they share one client record."""
    matched = doc.get("keywords", [])
    name_keywords = set(client.get("name_keywords", []))
    hit = next((k for k in matched if k in name_keywords), None)
    if hit:
        return (*CATEGORY_PERSON, hit)
    return (*CATEGORY_BRAND, client.get("name", ""))


def _apply_overrides(incident: dict, overrides: dict[str, Any]) -> dict:
    """`overrides` is flat: {"title": "...", "socialProfileInfo.location": "..."}
    -- each key a dotted path into `incident`, at most one level of nesting
    (the only nested object in this shape is socialProfileInfo)."""
    if not overrides:
        return incident
    out = {**incident, "socialProfileInfo": dict(incident.get("socialProfileInfo", {}))}
    for path, value in overrides.items():
        if "." in path:
            parent, child = path.split(".", 1)
            if isinstance(out.get(parent), dict):
                out[parent][child] = value
        else:
            out[path] = value
    return out


def build_incident_doc(doc: dict, client: dict) -> dict:
    platform = doc.get("platform", "")
    name = _display_name(doc)
    src_url = doc.get("url", "")
    asset_type = _asset_type(platform)
    category, sub_category, asset_name = _category_and_asset_name(doc, client)
    logo_match = bool(doc.get("logo_match"))
    username_match = bool(doc.get("username_match"))
    followers = doc.get("followers")
    location = doc.get("location") or None
    last_post = doc.get("last_post_date") or None
    active = _is_active(doc)

    risk = compute_incident_risk_score(
        has_logo=logo_match, username_match=username_match, followers=followers,
        location=location, last_post_iso=last_post, is_active=active,
    )

    incident = {
        "title": f"Similar {asset_type} Account {name} Found",
        "category": category,
        "subCategory": sub_category,
        "assetType": asset_type,
        "source": src_url,
        "date": None,
        "description": f"Name: {name} Url: {src_url}",
        "riskRating": str(risk),
        "domain": client.get("domain", ""),
        "orgId": client.get("client_id", ""),
        "assetCategory": asset_type,
        "assetName": asset_name,
        # no auto-detection signal for this exists anywhere in the tool --
        # purely an analyst call, defaulted off and only ever changed via
        # incident_overrides (the export's "ThirdParty YES/NO" column)
        "thirdParty": False,
        "socialProfileInfo": {
            "isActive": active,
            "isSimilarName": username_match,
            "isSimilarLogo": logo_match,
            "numberOfFollowers": followers,
            "profileName": doc.get("display_name") or None,
            "location": location,
            "profileImage": doc.get("profile_image_url") or None,
            "lastPostDate": last_post,
            "posts": None,
        },
    }
    return _apply_overrides(incident, doc.get("incident_overrides") or {})


async def publish_incident(doc: dict, client: dict) -> dict:
    return await incidents_db.upsert(build_incident_doc(doc, client))
