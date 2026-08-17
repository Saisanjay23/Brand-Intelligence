"""Shapes an analysed Profile document into the client-facing
published-incident record and upserts it into `published_incidents`,
keyed by source URL so a rediscovered/re-analysed profile updates its
existing record instead of duplicating it (see
database/repositories/published_incident_repository.py for the
diff-only write).

Deliberately triggered only by an explicit Publish (single "Publish Now"
or the bulk "Publish All"), never by analysis finishing on its own; the
publish-hold review step (ADR 0007) stays meaningful, and this is exactly
the moment a result actually leaves the tool for a client to see.

`build_incident_doc` is also called to render a live PREVIEW of this same
shape on every analysis-phase GET (see profile_service._to_full), so an
analyst can review/correct it before publishing, `doc["incident_overrides"]`
holds whatever fields an analyst has hand-edited (flat dotted keys, e.g.
"socialProfileInfo.location"), applied on top of the computed defaults
here, so an override always wins and survives a rediscovery/re-analysis
recomputing everything else.

`created_iso` (account creation date) is captured by some platform
engines' Row but is not one of ANALYSIS_FIELDS persisted on a Profile
document (see profile_repository.py), so "isActive" here can only be
judged from `last_post_date`, not from creation date as well, simply
because creation date is never stored.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from backend.database.repositories import published_incident_repository as incidents_db
from backend.platforms.registry import PLATFORMS
from backend.shared.models.scoring import compute_incident_risk_score
from backend.shared.models.scoring import ACTIVE_WINDOW_DAYS, resolve_match

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


def _is_recent(iso: Optional[str], *, days: int) -> Optional[bool]:
    """True/False when there is a date to judge, None when there isn't.

    The None case is load-bearing. Telegram exposes no last-post date for a
    user, Instagram's public profile payload often carries none, and any
    profile whose analysis was cut short has none either, and "we never
    got a date" is not the same claim as "this account is dormant". Folding
    the two together made every such incident assert `isActive: false`
    about an account nobody had actually checked, and depressed its
    riskRating on top of that.
    """
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days < days


def _is_active(doc: dict) -> Optional[bool]:
    return _is_recent(doc.get("last_post_date"), days=ACTIVE_WINDOW_DAYS)


def _first_match(matched: list, pool: set):
    """Case-insensitive first hit, a keyword saved as "Gautam Adani" and
    recorded on the profile as "gautam adani" is the same keyword, and an
    exact `in` comparison filed it under the wrong category."""
    lowered = {str(p).strip().lower() for p in pool}
    for k in matched:
        if str(k).strip().lower() in lowered:
            return k
    return None


def _category_and_asset_name(doc: dict, client: dict) -> tuple[str, str, str]:
    """(category, subCategory, assetName).

    assetName is the client's own name for a domain/brand-keyword match
    (the existing default), but the specific individual-name keyword that
    matched, not the client's name, for a person match: "adani" the
    company and "gautam adani" the person are different assets even
    though they share one client record.

    Two things this now handles that it previously got wrong:

    1. A profile with NO keywords at all. Anything added via
       `POST /profiles/add-urls` (an analyst pasting a tip) has an empty
       `keywords` array by construction, so it fell through to the brand
       default; every tip-sourced executive impersonation was filed as
       Brand Infringement. When there is nothing to classify by, the
       profile's own display name is matched against the client's
       individual-name keywords instead, which is the signal actually
       available on a hand-added URL.
    2. The client's curated Digital Risk Keyword lists
       (`asset_name_individual_keywords` / `asset_name_domain_keywords`)
       were stored, returned by the API, and shown as a dropdown, but never
       consulted here; so every incident needed a manual assetName
       override. They are now the preferred source, falling back to the
       previous behaviour when unset.
    """
    matched = list(doc.get("keywords") or [])
    name_keywords = set(client.get("name_keywords") or [])
    domain_keywords = set(client.get("domain_keywords") or [])
    drk_individual = list(client.get("asset_name_individual_keywords") or [])
    drk_domain = list(client.get("asset_name_domain_keywords") or [])

    name_hit = _first_match(matched, name_keywords)
    if not name_hit and not matched:
        # no keyword provenance (hand-added URL), fall back to what the
        # profile itself is calling itself
        display = str(doc.get("display_name") or doc.get("username") or "").lower()
        if display:
            name_hit = next(
                (k for k in name_keywords if str(k).strip().lower() in display), None
            )

    if name_hit:
        # prefer the client's curated individual DRK entry when one is
        # configured; otherwise the matched keyword itself, as before
        asset = _first_match([name_hit], set(drk_individual)) or (drk_individual[0] if len(drk_individual) == 1 else name_hit)
        return (*CATEGORY_PERSON, asset)

    domain_hit = _first_match(matched, domain_keywords)
    asset = _first_match([domain_hit] if domain_hit else [], set(drk_domain))
    if not asset:
        asset = drk_domain[0] if len(drk_domain) == 1 else client.get("name", "")
    return (*CATEGORY_BRAND, asset)


# the scraped fields that make up a reading, for the evidence block's
# "how much of this did we actually see" count
_EVIDENCE_FIELDS = (
    "display_name", "followers", "location", "last_post_date",
    "profile_image_url", "entity_type", "verified",
)


def _fields_read(doc: dict) -> int:
    return sum(1 for f in _EVIDENCE_FIELDS if doc.get(f) not in (None, "", [], {}))


def _finding_date(doc: dict) -> Optional[str]:
    """ISO date this finding was established, when analysis read the
    profile, falling back to when discovery first saw it."""
    for field in ("analysed_at", "last_seen", "first_seen"):
        v = doc.get(field)
        if isinstance(v, datetime):
            return v.astimezone(timezone.utc).isoformat(timespec="seconds") if v.tzinfo else v.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        if isinstance(v, str) and v:
            return v
    return None


def _apply_overrides(incident: dict, overrides: dict[str, Any]) -> dict:
    """`overrides` is flat: {"title": "...", "socialProfileInfo.location": "..."}
   ; each key a dotted path into `incident`, at most one level of nesting
    (the only nested object in this shape is socialProfileInfo)."""
    if not overrides:
        return incident
    out = {**incident, "socialProfileInfo": dict(incident.get("socialProfileInfo", {}))}
    for path, value in overrides.items():
        if path in ("socialProfileInfo.isSimilarName", "socialProfileInfo.isSimilarLogo"):
            continue  # ignore legacy cosmetic overrides; keep table match toggles authoritative
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
    overrides = doc.get("incident_overrides") or {}
    # One rule for what "matched" means, shared with the tool's own internal
    # risk_score (shared/models/scoring.py::resolve_match): the analyst's own
    # call wins either way, a validated profile counts as matched by default,
    # and otherwise the scraper's automated signal stands. This used to be
    # resolved two different ways in this one function; the logo ignored
    # `has_logo` entirely while the name OR-ed the analyst's value with
    # `has_name_match`; so the same profile could score differently here
    # than in the tool, and undoing a name match couldn't lower the published
    # risk rating at all.
    validated = doc.get("status") == "approved"
    logo_match = overrides.get(
        "socialProfileInfo.isSimilarLogo",
        resolve_match(bool(doc.get("has_logo")), doc.get("logo_match"), validated),
    )
    name_match = overrides.get(
        "socialProfileInfo.isSimilarName",
        resolve_match(bool(doc.get("has_name_match")), doc.get("username_match"), validated),
    )
    followers = overrides.get("socialProfileInfo.numberOfFollowers", doc.get("followers"))
    location = overrides.get("socialProfileInfo.location", doc.get("location") or None)
    last_post = overrides.get("socialProfileInfo.lastPostDate", doc.get("last_post_date") or None)
    active = overrides.get("socialProfileInfo.isActive", _is_active(doc))

    risk = compute_incident_risk_score(
        has_logo=logo_match, has_name_match=name_match, followers=followers,
        location=location, last_post_iso=last_post, is_active=bool(active),
    )

    incident = {
        "title": f"Similar {asset_type} Account {name} Found",
        "category": category,
        "subCategory": sub_category,
        "assetType": asset_type,
        "source": src_url,
        # when this finding was established, i.e. when analysis actually
        # read the profile (falling back to first discovery). Was hard-coded
        # None, so every published incident arrived undated and a consumer
        # had no way to tell a finding from this morning from one from March.
        "date": _finding_date(doc),
        "description": f"Name: {name} Url: {src_url}",
        "riskRating": str(risk),
        "domain": client.get("domain", ""),
        "orgId": client.get("client_id", ""),
        "assetCategory": asset_type,
        "assetName": asset_name,
        # no auto-detection signal for this exists anywhere in the tool,
        # purely an analyst call, defaulted off and only ever changed via
        # incident_overrides (the export's "ThirdParty YES/NO" column)
        "thirdParty": False,
        "socialProfileInfo": {
            # null, not false, when there is no last-post date to judge by
            # see _is_recent. A consumer that treats null as "unknown" now
            # can; one that coerces to falsey behaves exactly as before.
            "isActive": active,
            "isSimilarName": name_match,
            "isSimilarLogo": logo_match,
            "numberOfFollowers": followers,
            "profileName": doc.get("display_name") or doc.get("username") or None,
            "location": location,
            "profileImage": doc.get("profile_image_url") or None,
            "lastPostDate": last_post,
            "posts": None,
        },
        # How much of this record rests on something actually read, versus
        # absent. `riskRating` alone states the same confidence whether five
        # fields were scraped or one, which is the difference between a
        # finding and a guess.
        "evidence": {
            "screenshot": bool(doc.get("screenshot")),
            "fieldsRead": _fields_read(doc),
            "analysisStatus": doc.get("analysis_status") or "",
        },
    }
    return _apply_overrides(incident, doc.get("incident_overrides") or {})


async def publish_incident(doc: dict, client: dict) -> dict:
    return await incidents_db.upsert(build_incident_doc(doc, client))


async def list_published(org_id: str, platform: Optional[str] = None, limit: int = 50, offset: int = 0) -> dict:
    """Findings already written by `publish_incident`, for a caller that
    wants the durable record rather than the live preview on a profile
    (see `profile_service._to_full`'s `incident` field). `platform` is a
    platform id (e.g. "twitter"); resolved here to the display name
    (`assetType`) actually stored on a published-incident document."""
    asset_type = PLATFORMS[platform].name if platform and platform in PLATFORMS else platform
    items, total = await incidents_db.find(org_id, platform=asset_type, limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}
