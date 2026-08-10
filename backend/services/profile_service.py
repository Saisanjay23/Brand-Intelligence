"""Profile business logic -- extracted from the old `routes_profiles.py`
handler bodies so the controller layer stays request-in/response-out only.

Two response shapes off the same collection, picked by `phase`:
`phase` omitted/`discovery` -> the triage/card list an analyst reviews
and a UI renders as cards (profile_name + logo, nothing else).
`phase=analysis` -> the final result: client + keyword context inlined
onto each validated, scraped profile (a client has one name, fetched
once per request rather than stored redundantly on every profile).

Approving a profile here is also where analysis gets queued -- the only
place in this layer that reaches into the job service directly, since
that's exactly the "approve -> auto-launch analysis for that profile's
own platform" behavior described in the plan.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.database.repositories import client_repository as clients_db
from backend.database.repositories import profile_repository as profiles_db
from backend.services import incident_publisher
from backend.services.job_service import ANALYSIS, job_manager
from backend.shared.errors import NotFoundError
from backend.shared.logging import get_logger
from backend.validators.profile_validator import validate_patch_fields

log = get_logger("services.profile_service")


def _to_card(doc: dict) -> dict:
    return {
        "id": doc["id"], "platform": doc.get("platform", ""), "url": doc.get("url", ""),
        "profile_name": doc.get("display_name") or doc.get("username") or "",
        "profile_image_url": doc.get("profile_image_url", ""),
        "has_logo": bool(doc.get("has_logo", False)),
        "verified": bool(doc.get("verified", False)),
        "status": doc.get("status", "pending"),
        "phase": doc.get("phase", "discovery"),
        "risk_score": doc.get("risk_score"),
        "priority": doc.get("priority"),
        "comments": doc.get("comments"),
        "followers": doc.get("followers"),
        # "profile" | "page" -- only Facebook discovery distinguishes these
        # (see PLATFORM_TABS in discovery_service.py); every other platform
        # only ever sweeps one entity type, so this is blank there.
        "entity_type": doc.get("entity_type", ""),
        # every keyword sweep that has ever (re)found this profile -- lets
        # the discovery filter narrow to "only what THIS keyword matched"
        "keywords": doc.get("keywords", []),
        # 0-100 name-vs-keyword closeness, seeded at discovery time and
        # refined once analysis actually visits the profile -- the card's
        # High/Low match badge
        "name_score": doc.get("name_score"),
        # 0-100 handle-vs-official-handle closeness. None means the client
        # has no official handle set for this platform, i.e. NOT MEASURED --
        # deliberately distinct from 0 ("measured, no resemblance"), so the
        # UI can say "not configured" rather than implying a clean result.
        "username_score": doc.get("username_score"),
        # an analyst's own visual confirmation, set via the card's Validate
        # action -- see EDITABLE in profile_repository.py
        "logo_match": doc.get("logo_match"),
        "username_match": doc.get("username_match"),
        # set only on a profile a rediscovery just bounced from "rejected"
        # back to "pending" (see profile_repository.save()'s reconsideration
        # branch) -- the old/new value per changed field, so an analyst
        # seeing a profile reappear in their pending queue can see WHY
        # instead of having to trust it blindly or dig through Mongo.
        # Cleared the moment the analyst makes a fresh decision (patch()
        # unsets it on any status change), so it never lingers as stale info.
        "changes": doc.get("changes"),
        "changed_at": doc.get("changed_at"),
    }


def _to_full(doc: dict, client: Optional[dict]) -> dict:
    return {
        "id": doc["id"], "client_id": doc.get("client_id", ""),
        "client_name": client["name"] if client else "",
        "keyword": ", ".join(doc.get("keywords", [])),
        "platform": doc.get("platform", ""), "url": doc.get("url", ""),
        "username": doc.get("username") or doc.get("display_name") or "",
        "profile_name": doc.get("display_name") or doc.get("username") or "",
        "profile_image_url": doc.get("profile_image_url", ""),
        "followers": doc.get("followers"), "location": doc.get("location", ""),
        "last_post_date": doc.get("last_post_date"),
        # True/False/None -- computed from last_post_date against the
        # 6-month industry-standard dormant-account window (see
        # shared/models/scoring.py::ACTIVE_WINDOW_DAYS and
        # shared/models/row.py::Row.active_yes). None means "no last-post
        # date was ever found," not "confirmed inactive" -- was already
        # computed and used for risk scoring, just never reached this
        # response before.
        "is_active": doc.get("is_active"),
        "has_logo": bool(doc.get("has_logo", False)),
        "verified": bool(doc.get("verified", False)),
        "status": doc.get("status", "pending"),
        "phase": doc.get("phase", "analysis"),
        "risk_score": doc.get("risk_score"),
        "priority": doc.get("priority"),
        "comments": doc.get("comments"),
        "published": doc.get("published", True),
        "publish_hold_until": doc.get("publish_hold_until"),
        # Evidence capture -- the screenshot taken while analysis was
        # actually looking at the profile. An impersonating account is
        # routinely suspended or deleted before anyone downstream reads the
        # incident, so this is frequently the only surviving proof it
        # existed, and it is what a platform takedown team or a lawyer
        # actually asks for. The stored value is a path relative to the
        # evidence root and is never exposed; the API hands out a URL that
        # goes through the guarded route instead.
        "screenshot_url": f"/profiles/{doc['id']}/screenshot" if doc.get("screenshot") else None,
        "screenshot_at": doc.get("screenshot_at"),
        # analysis outcome + how many attempts it took, so an analyst can
        # tell "we read this profile" from "we tried and never got in"
        "analysis_status": doc.get("analysis_status", ""),
        "analysis_attempts": doc.get("analysis_attempts", 0),
        "analysed_at": doc.get("analysed_at"),
        # see _to_card's identical field -- carried onto the analysis record
        # so the handle signal stays visible after a profile is validated
        "username_score": doc.get("username_score"),
        # carried over unchanged from the discovery card's Validate action --
        # "logo match yes / username match yes" stays visible on the
        # analysis-phase record too
        "logo_match": doc.get("logo_match"),
        "username_match": doc.get("username_match"),
        # see _to_card's identical field -- reconsideration can bounce an
        # already-analysed (phase=analysis) rejected profile back to
        # pending too, not just a discovery-phase one.
        "changes": doc.get("changes"),
        "changed_at": doc.get("changed_at"),
        # a live preview of the exact record Publish will write to
        # published_incidents -- editable via PATCH's incident_overrides
        # (see profile_repository.EDITABLE) before the analyst actually
        # publishes. None when the client record is gone (nothing to
        # compute domain/orgId/assetName from).
        "incident": incident_publisher.build_incident_doc(doc, client) if client else None,
    }


async def cleanup_stale_pending(days: int = 60) -> dict:
    return {"deleted": await profiles_db.cleanup_stale_pending(days)}


async def archive_stale_rejected(days: int = 180) -> dict:
    return {"archived": await profiles_db.archive_stale_rejected(days)}


async def list_profiles(
    client_id: str, status: Optional[str] = None, phase: Optional[str] = None,
    platform: Optional[str] = None, limit: int = 100, offset: int = 0,
    include_held: bool = False, keyword: Optional[str] = None,
    entity_type: Optional[str] = None, priority: Optional[str] = None,
    match_level: Optional[str] = None, keyword_match_type: Optional[str] = None,
    search: Optional[str] = None,
) -> dict:
    # `keyword_match_type` classifies against the CLIENT's own configured
    # keyword lists, so the client record has to be loaded before the query
    # rather than after it (the analysis branch below needs it anyway).
    client = None
    if keyword_match_type or phase == profiles_db.PHASE_ANALYSIS:
        client = await clients_db.try_get(client_id)

    docs, total, counts = await profiles_db.find(
        client_id, platform=platform, status=status, phase=phase, limit=limit, offset=offset,
        include_held=include_held, keyword=keyword, entity_type=entity_type,
        priority=priority, match_level=match_level, keyword_match_type=keyword_match_type,
        search=search, client_keywords=client,
    )
    if phase == profiles_db.PHASE_ANALYSIS:
        items = [_to_full(d, client) for d in docs]
    else:
        items = [_to_card(d) for d in docs]
    return {"items": items, "total": total, "counts": counts, "limit": limit, "offset": offset}


async def screenshot_path(profile_id: str) -> tuple[str, str]:
    """(absolute path, download filename) for a profile's evidence capture.

    The stored value is deliberately relative to `settings.evidence_path`,
    and this is the only place it is ever resolved back to a real path. The
    containment check is not a formality: the stored string reaches Mongo
    from scraping code, and joining an unvalidated relative path onto a root
    is exactly how `../../etc/passwd` becomes a file read.
    """
    from backend.config.settings import settings

    doc = await profiles_db.get_by_id(profile_id)
    if doc is None:
        raise NotFoundError(f"profile {profile_id!r} not found")
    rel = (doc.get("screenshot") or "").strip()
    if not rel:
        raise NotFoundError(f"profile {profile_id!r} has no evidence screenshot")

    root = settings.evidence_path.resolve()
    try:
        target = (root / rel).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        log.warning(f"profile {profile_id}: screenshot path {rel!r} escapes the evidence root -- refusing")
        raise NotFoundError(f"profile {profile_id!r} has no readable evidence screenshot")
    if not target.is_file():
        # the document points at a capture that is no longer on disk (volume
        # replaced, evidence pruned) -- a 404 with a clear reason, not a 500
        raise NotFoundError(f"profile {profile_id!r} evidence file is missing from the evidence store")

    platform = doc.get("platform", "profile")
    name = doc.get("display_name") or doc.get("username") or doc.get("entity_id") or profile_id
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{platform}_{name}")[:80]
    return str(target), f"{safe}.png"


async def stats(client_id: str, platform: Optional[str] = None) -> dict:
    """One aggregate query the dashboard can call instead of firing five-plus
    separate `list_profiles` calls (one per status, one per platform) just to
    show summary counts -- see `profiles_db.stats`, which was already
    implemented but had no route exposing it."""
    return await profiles_db.stats(client_id, platform)


async def coverage(client_id: str, platform: Optional[str] = None) -> dict:
    """Did we actually check everything we said we would?

    This is the question that decides whether a client report is finished,
    and nothing in the tool could answer it. Volume counts ("200 analysed")
    look identical whether the other 60 approved profiles are still queued,
    permanently failed, or never existed.

    `complete` is deliberately conservative: anything still owed or given up
    on makes it false. `blocked` names the profiles that will NOT be retried
    without intervention, because a silent permanent skip on an approved
    impersonation candidate is the worst outcome this tool can produce.
    """
    counts = await profiles_db.stats(client_id, platform)
    blocked = await profiles_db.stuck_analysis(client_id, platform)
    approved = counts.get("approved", 0)
    awaiting = counts.get("awaiting_analysis", 0)
    failed = counts.get("analysis_failed", 0)
    return {
        "approved": approved,
        "analysed": max(0, approved - awaiting - failed),
        "awaiting_analysis": awaiting,
        "analysis_failed": failed,
        "held": counts.get("held", 0),
        "with_evidence": counts.get("with_evidence", 0),
        "complete": awaiting == 0 and failed == 0,
        "blocked": [
            {
                "id": b["id"], "url": b.get("url", ""), "platform": b.get("platform", ""),
                "profile_name": b.get("display_name", ""),
                "reason": b.get("analysis_status", ""), "attempts": b.get("analysis_attempts", 0),
                "detail": b.get("comments", ""),
            }
            for b in blocked
        ],
    }


async def get_profile(profile_id: str) -> dict:
    doc = await profiles_db.get_by_id(profile_id)
    if doc is None:
        raise NotFoundError(f"profile {profile_id!r} not found")
    client = await clients_db.try_get(doc.get("client_id", ""))
    return _to_full(doc, client)


# host -> platform id, for auto-detecting which scraper a hand-typed URL
# belongs to. Deliberately the same platform id vocabulary as
# backend/platforms/registry.py -- these ARE looked up there right after.
_PLATFORM_HOSTS: dict[str, str] = {
    "facebook.com": "facebook", "fb.com": "facebook", "fb.me": "facebook",
    "twitter.com": "twitter", "x.com": "twitter",
    "instagram.com": "instagram",
    "youtube.com": "youtube", "youtu.be": "youtube",
    "t.me": "telegram", "telegram.me": "telegram",
}


def _parse_manual_url(raw: str) -> Optional[tuple[str, str, str]]:
    """(platform, normalized_url, entity_id) for one hand-typed URL, or None
    if its host doesn't match a supported platform. Facebook gets its own
    real id/url normalization (the same one discovery uses, so a manually
    added Facebook URL dedupes against an already-discovered card instead
    of creating a second row for the same profile); every other platform
    falls back to the same "last non-empty path segment" heuristic
    discovery_service.py's _hit_to_fields already uses for its own
    non-Facebook rows, so a manually added URL lands in the exact same
    shape a normal sweep would have produced for it."""
    from urllib.parse import urlparse

    raw = raw.strip()
    if not raw:
        return None
    url = raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
    host = urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    platform = _PLATFORM_HOSTS.get(host, "")
    if not platform:
        return None
    if platform == "facebook":
        from backend.platforms.facebook.discovery_engine import normalize_url, profile_id
        url = normalize_url(url)
        return platform, url, profile_id(url)
    parts = [s for s in urlparse(url).path.rstrip("/").split("/") if s]
    entity_id = parts[-1].lstrip("@") if parts else ""
    return platform, url, entity_id


async def add_manual_urls(client_id: str, urls: list[str]) -> dict:
    """An analyst who already has a specific profile URL in hand (a tip, a
    report, a link an earlier sweep never turned up) shouldn't have to
    reverse-engineer a keyword that would make discovery happen to find it
    again. Each URL is created (or matched onto an existing card) straight
    at status="approved" -- typing the URL in IS the approval decision, so
    it goes to analysis without an extra manual Validate click, exactly
    like any other approved profile (see the same auto-queue this module's
    patch_profile/bulk_patch_profiles already trigger on approval) -- but
    never overrides a profile that was already explicitly rejected/approved
    by an earlier decision (see profile_repository.save's initial_status
    guard).

    One bad URL never sinks the batch; unsupported/unparseable ones come
    back in `skipped` rather than raising.
    """
    from backend.platforms import registry
    from backend.services import client_service

    client = await client_service.get(client_id)  # 404s if the client was never configured
    client_keywords = list(client.get("name_keywords") or []) + list(client.get("domain_keywords") or [])

    added_by_platform: dict[str, int] = {}
    skipped: list[str] = []
    for raw in urls:
        parsed = _parse_manual_url(raw)
        if not parsed or parsed[0] not in registry.PLATFORMS:
            skipped.append(raw)
            continue
        platform, url, entity_id = parsed
        # Attach keyword provenance where the URL itself reveals it. A
        # hand-added profile has no sweep behind it, so `keywords` was
        # always empty -- and incident_publisher classifies an incident
        # (person vs brand) from exactly that array, so EVERY tip-sourced
        # executive impersonation was published as Brand Infringement.
        # Matching the client's own keywords against the URL recovers the
        # provenance when it is there to recover; when it isn't, the
        # publisher falls back to the scraped display name once analysis
        # has run.
        haystack = f"{url} {entity_id}".lower()
        matched = next(
            (k for k in client_keywords
             if k and re.sub(r"[^a-z0-9]", "", str(k).lower()) and
             re.sub(r"[^a-z0-9]", "", str(k).lower()) in re.sub(r"[^a-z0-9]", "", haystack)),
            "",
        )
        # entity_type ("profile" vs "page") deliberately left unset here --
        # a URL alone can't reliably tell a Facebook Page from a personal
        # profile, and guessing wrong would misfile it under the People-
        # Only/Pages-Only filter. Blank reads honestly as "unknown", same
        # as any other field this can't determine yet.
        await profiles_db.save(
            client_id, platform, profiles_db.PHASE_DISCOVERY,
            {"discovery_source": "manual"},
            url=url, entity_id=entity_id, keyword=matched, initial_status="approved",
        )
        added_by_platform[platform] = added_by_platform.get(platform, 0) + 1

    for platform in added_by_platform:
        job_manager.create(ANALYSIS, client_id, {}, platform=platform)

    return {"added": sum(added_by_platform.values()), "by_platform": added_by_platform, "skipped": skipped}


async def patch_profile(profile_id: str, body_fields: dict) -> dict:
    fields = validate_patch_fields({k: v for k, v in body_fields.items() if v is not None})
    updated = await profiles_db.patch(profile_id, fields)
    if fields.get("status") == "approved":
        job_manager.create(ANALYSIS, updated["client_id"], {}, platform=updated.get("platform"))
    # same curated shape GET /profiles/{id} returns -- a PATCH response must
    # look like the record it just changed, not a different (raw-document)
    # shape with internal fields and none of _to_full's computed ones
    client = await clients_db.try_get(updated.get("client_id", ""))
    return _to_full(updated, client)


async def bulk_patch_profiles(
    profile_ids: list[str], status: str,
    logo_match: Optional[bool] = None, username_match: Optional[bool] = None,
) -> dict:
    """The bulk counterpart to `patch_profile` -- one request instead of one
    per card, for an analyst triaging hundreds of discovered profiles. One
    bad id never sinks the batch; each is applied independently and
    reported back in `succeeded`/`failed`.

    Deliberately does NOT just call `patch_profile` in a loop for the
    auto-queue-analysis side effect: that would fire one analysis job per
    approved profile, and approving 200 profiles from the same platform at
    once would flood the job queue with 200 redundant jobs all competing for
    that platform's single lock (see job_service.py). Instead it queues at
    most one job per distinct (client, platform) pair actually touched.
    """
    fields = validate_patch_fields({
        k: v for k, v in {"status": status, "logo_match": logo_match, "username_match": username_match}.items()
        if v is not None
    })

    async def _one(pid: str) -> tuple[str, Optional[dict]]:
        try:
            return pid, await profiles_db.patch(pid, fields)
        except Exception as e:
            log.warning(f"bulk patch failed for {pid}: {type(e).__name__}: {e}")
            return pid, None

    results = await asyncio.gather(*(_one(pid) for pid in profile_ids))
    succeeded = [(pid, doc) for pid, doc in results if doc is not None]
    failed = [pid for pid, doc in results if doc is None]

    if status == "approved":
        queued: set[tuple[str, str]] = set()
        for _, doc in succeeded:
            key = (doc.get("client_id", ""), doc.get("platform", ""))
            if key not in queued:
                queued.add(key)
                job_manager.create(ANALYSIS, key[0], {}, platform=key[1] or None)

    return {"succeeded": [pid for pid, _ in succeeded], "failed": failed}


async def publish_profile(profile_id: str) -> dict:
    """An analyst confirming a held analysis result before its hold clears
    on its own -- see ADR 0007. Publishing is also the one moment this
    tool builds/upserts the client-facing incident record for the profile
    (see services/incident_publisher.py) -- never at analysis time, so the
    hold review stays meaningful."""
    updated = await profiles_db.publish(profile_id)
    client = await clients_db.try_get(updated.get("client_id", ""))
    if client:
        await incident_publisher.publish_incident(updated, client)
    return _to_full(updated, client)


_PUBLISH_MODE_WINDOWS = {
    "recent": timedelta(hours=24),
    "2days": timedelta(days=2),
    "week": timedelta(days=7),
}


async def publish_all_profiles(client_id: str, platform: Optional[str] = None, mode: str = "all") -> dict:
    """Publishes every analysis-phase profile for this client (optionally
    scoped to one platform, and optionally scoped to a recent analysis
    window -- "recent"=last 24h, "2days", "week") that isn't already
    published -- the bulk counterpart to publish_profile, same
    incident-record side effect per profile. The cutoff is always computed
    here, server-side, from `mode` -- never trusts a client-supplied
    timestamp."""
    since = None
    window = _PUBLISH_MODE_WINDOWS.get(mode)
    if window:
        since = datetime.now(timezone.utc) - window
    ids = await profiles_db.list_unpublished_ids(client_id, platform, since)
    client = await clients_db.try_get(client_id)
    published = 0
    for profile_id in ids:
        updated = await profiles_db.publish(profile_id)
        if client:
            await incident_publisher.publish_incident(updated, client)
        published += 1
    return {"published": published}
