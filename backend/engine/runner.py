"""The standalone engine: input in, results out, nothing else required.

WHY THIS EXISTS, given `services/discovery_service.py` and
`services/analysis_service.py` already orchestrate the same adapters: those
two take a `Job`, read the client's keywords and caps out of `clients`, and
write every result into `profiles`, they cannot run at all without Mongo,
and they hand a caller nothing back. This module drives the SAME platform
adapters over the same contracts (`platforms/contracts.py`) and RETURNS the
results instead of storing them.

Nothing here imports `backend.database`, `motor`, `fastapi` or `Job`, and a
test in `tests_unit/` asserts that stays true, an accidental import would
silently re-introduce the dependency this package exists to avoid.

WHAT IS AND ISN'T DUPLICATED. The scraping, parsing, scoring, stealth and
session-construction logic is the existing code, imported and called
unchanged; a platform fix lands in both paths at once. What is restated
here is the ~40 lines of field mapping (`_hit_to_fields`/`_row_to_fields`)
that live inside those Mongo-bound service modules and cannot be imported
without dragging Motor in. `tests_unit/` pins the two copies together.

WHAT THIS DELIBERATELY DOES NOT DO, because each needs durable state that
only the database provides: cross-run deduplication, publish-hold, incident
recording, the analyst approve/reject workflow, scheduled sweeps, and
least-recently-used session rotation with persistent backoff. A standalone
run is one shot: it scrapes what you asked for and gives it to you.
"""

from __future__ import annotations

import asyncio
import inspect
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

from backend.config.settings import settings
from backend.engine.credentials import READY, CredentialStore
from backend.engine.models import (
    AnalysisRequest,
    DiscoveryRequest,
    EngineResult,
    PlatformOutcome,
    platform_for_url,
)
from backend.platforms import registry
from backend.platforms.scan_options import DiscoveryOptions, ScanOptions
from backend.shared.completeness import field_report, missing_fields
from backend.shared.logging import get_logger
from backend.shared.models.row import Row
from backend.shared.text import contiguous_letters_match, name_score

log = get_logger("engine.runner")

# Called with a short human-readable line as the run proceeds. The API path
# emits structured job events for a polling frontend; standalone there is
# nobody to poll, so progress is a callback the caller may print, log, or
# ignore entirely.
Progress = Optional[Callable[[str], Any]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _say(on_progress: Progress, message: str) -> None:
    """Progress reporting must never be able to break a scrape, a caller's
    own callback raising, or being a coroutine function, is their business
    and not a reason to lose a run's results."""
    log.info(message)
    if on_progress is None:
        return
    try:
        out = on_progress(message)
        if inspect.isawaitable(out):
            await out
    except Exception as e:
        log.debug(f"progress callback raised, ignoring: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- mapping


def _username_from(url: str, platform: str) -> str:
    """Mirrors `services/discovery_service.py::_hit_to_fields`'s username
    derivation, including its fix for a URL with no path at all returning
    the HOSTNAME as the username."""
    if platform == "facebook":
        from backend.platforms.facebook.discovery_engine import profile_id

        return profile_id(url or "")
    parsed = urlparse(url if "//" in url else f"//{url}")
    parts = [s for s in parsed.path.split("/") if s]
    if not parts:
        return ""
    candidate = parts[-1].lstrip("@")
    return candidate if "?" not in candidate and "#" not in candidate else ""


def _hit_to_fields(hit, platform: str, target: str) -> dict:
    """A discovery Hit -> the same field dict the Mongo path stores.

    `target` rather than the keyword drives `name_score` when the caller
    supplied one: standalone runs are routinely swept with several
    spelling variants of one brand, and scoring each hit against whichever
    variant happened to surface it would rate the same profile differently
    depending on which keyword found it first.
    """
    fields = {
        "platform": platform,
        "url": hit.url or "", "entity_id": hit.entity_id, "keyword": hit.keyword,
        "username": _username_from(hit.url or "", platform),
        "display_name": hit.name, "entity_type": hit.entity_type,
        "discovery_source": hit.source, "profile_image_url": getattr(hit, "avatar", ""),
        "has_logo": bool(getattr(hit, "has_custom_pic", False)),
        "name_score": name_score(hit.name or "", target or hit.keyword or ""),
        "name_exact_run": contiguous_letters_match(hit.name or "", target or hit.keyword or ""),
        "tab": hit.tab, "rank": hit.rank,
    }
    # See services/discovery_service.py::_hit_to_fields, only include the
    # key when a badge was actually detected, so a rediscovery sweep can
    # never overwrite an already-confirmed verified=True back to False.
    if getattr(hit, "verified", False):
        fields["verified"] = True
    return fields


def _tri(flag: str) -> Optional[bool]:
    """Row's "Yes"/"No"/"" -> True/False/None. The empty string means the
    scraper could not determine the field, which is NOT the same as
    determining it false, collapsing the two publishes "this account is
    inactive" about profiles whose last-post date was never visible.
    Same reasoning as `services/analysis_service.py::_tri`."""
    return True if flag == "Yes" else False if flag == "No" else None


def _row_to_fields(row: Row, platform: str, evidence_root: Optional[Path]) -> dict:
    """An analysis Row -> the same field dict the Mongo path stores.

    The screenshot is reported relative to the evidence directory actually
    used, falling back to the absolute path when it landed somewhere else.
    Standalone the absolute path is not the leak it is over an HTTP API
    (see `analysis_service._relative_screenshot`), the caller owns the
    filesystem, but a relative path still travels better between machines.
    """
    shot = row.screenshot or ""
    if shot and evidence_root:
        try:
            shot = Path(shot).resolve().relative_to(evidence_root.resolve()).as_posix()
        except (ValueError, OSError):
            pass
    return {
        "platform": platform,
        "url": row.url, "entity_id": row.profile_id, "keyword": "",
        "username": _username_from(row.url, platform),
        "display_name": row.profile_name, "entity_type": row.entity_type,
        "target": row.target, "official_feed": row.original_feed,
        "followers": row.followers, "followers_exact": row.followers_exact,
        "friends": row.friends, "location": row.location,
        "profile_image_url": row.profile_pic_url,
        # row.verified is None when this platform's analysis engine never
        # checked for a badge, passed through as-is (see
        # services/analysis_service.py::_row_to_fields for why).
        "has_logo": _tri(row.logo_yes), "verified": row.verified,
        "is_active": _tri(row.active_yes),
        "has_name_match": _tri(row.name_yes), "name_score": row.name_score,
        "name_exact_run": row.name_exact_run,
        "last_post_date": row.last_post_iso, "created": row.created_iso,
        "risk_score": row.risk, "priority": row.priority,
        "comments": row.notes, "analysis_status": row.status, "sources": dict(row.src),
        "screenshot": shot,
        # Kept in step with services/analysis_service.py::_row_to_fields
        # (tests_unit/test_engine_standalone.py asserts the two agree): a
        # row that was reached but came away short of a field the platform
        # publishes is not a finished reading. See shared/completeness.py.
        "analysis_complete": not missing_fields(
            platform, row, want_screenshot=bool(evidence_root)),
        # Kept in step with services/analysis_service.py::_row_to_fields --
        # the two signals that make a blank explainable instead of ambiguous.
        "posts_seen": row.posts_seen,
        "field_status": field_report(
            platform, row, want_screenshot=bool(evidence_root)),
    }


# ------------------------------------------------------------- discovery


async def discover(
    request: DiscoveryRequest,
    store: Optional[CredentialStore] = None,
    on_progress: Progress = None,
) -> EngineResult:
    """Keywords -> candidate profiles, across every requested platform.

    One platform's session dying never aborts the others: each is run
    inside its own try/except and recorded as its own `PlatformOutcome`,
    the same isolation the Mongo-backed sweep provides.
    """
    store = store or CredentialStore()
    started = time.monotonic()
    result = EngineResult(kind="discovery", started_at=_now_iso())

    if not request.keywords:
        result.errors.append("no keywords given -- nothing to sweep")
        result.finished_at, result.seconds = _now_iso(), time.monotonic() - started
        return result

    wanted = request.platforms or [
        pid for pid, p in registry.PLATFORMS.items() if p.enabled and p.can_discover
    ]
    runnable: list[str] = []
    for platform_id in wanted:
        if platform_id not in registry.PLATFORMS:
            result.platforms.append(PlatformOutcome(platform_id, "skipped", "unknown platform"))
            continue
        plat = registry.get(platform_id)
        if not plat.enabled:
            result.platforms.append(PlatformOutcome(platform_id, "skipped", "platform disabled"))
        elif not plat.can_discover:
            result.platforms.append(PlatformOutcome(platform_id, "skipped", "no discovery phase"))
        elif store.state_for(platform_id) != READY:
            result.platforms.append(PlatformOutcome(
                platform_id, "skipped", f"{store.state_for(platform_id)} -- {store.why_not(platform_id)}"))
        else:
            runnable.append(platform_id)

    if not runnable:
        result.errors.append(
            "no platform has usable credentials -- run `python -m backend.engine platforms` to see what is missing")
        result.finished_at, result.seconds = _now_iso(), time.monotonic() - started
        return result

    target = request.resolved_target()
    await _say(on_progress, f"discovery: {len(request.keywords)} keyword(s) x {len(runnable)} platform(s)")

    gate = asyncio.Semaphore(max(1, request.platform_concurrency))

    async def _one(platform_id: str) -> tuple[PlatformOutcome, list[dict]]:
        async with gate:
            return await _discover_platform(platform_id, request, target, store, on_progress)

    for outcome, profiles in await asyncio.gather(*(_one(p) for p in runnable)):
        result.platforms.append(outcome)
        result.profiles.extend(profiles)

    result.finished_at, result.seconds = _now_iso(), time.monotonic() - started
    await _say(on_progress, result.summary())
    return result


async def _discover_platform(
    platform_id: str, request: DiscoveryRequest, target: str,
    store: CredentialStore, on_progress: Progress,
) -> tuple[PlatformOutcome, list[dict]]:
    plat = registry.get(platform_id)
    tabs = request.tabs_for(platform_id)
    units = max(1, len(request.keywords) * len(tabs))
    outcome = PlatformOutcome(platform_id, "failed", total=units)
    began = time.monotonic()
    # Deduplicated across every (keyword, tab) sweep on this platform: the
    # same account routinely surfaces under several keywords, and a caller
    # wants one record per profile carrying the first keyword that found it,
    # not one record per way it was found.
    seen: dict[str, dict] = {}

    options = DiscoveryOptions(
        concurrency=request.concurrency,
        max_results=request.max_results,
        max_seconds=request.max_seconds,
        headful=request.headful,
    )

    for item in store.sessions_for(platform_id):
        session = None
        discoverer = None
        outcome.session = item["identifier"]
        try:
            ctx = None
            if plat.session_path:
                session = plat.session_cls()(
                    options, item.get("cookies", []),
                    session_id=item.get("id", ""), proxy=item.get("proxy"),
                )
                ctx = await session.start()
                if not await session.check_session():
                    store.burn(item["id"], "session rejected at check_session")
                    outcome.reason = f"session {item['identifier']} invalid or checkpointed"
                    continue  # rotate to the next credential
                await _say(on_progress, f"[{platform_id}] session {item['identifier']} ok")

            discoverer = plat.discoverer()(options, ctx)
            accepts_progress = "on_progress" in inspect.signature(discoverer.sweep).parameters

            for keyword in request.keywords:
                for tab in tabs:
                    try:
                        sweep = (
                            await discoverer.sweep(keyword, tab, on_progress=None)
                            if accepts_progress else await discoverer.sweep(keyword, tab)
                        )
                    except Exception as e:  # one keyword must not lose the rest
                        log.error(f"[{platform_id}] {keyword!r}/{tab} failed: {traceback.format_exc()}")
                        outcome.reason = f"{type(e).__name__}: {e}"
                        continue
                    outcome.attempted += 1
                    for hit in sweep.hits or []:
                        if hit.entity_id and hit.entity_id not in seen:
                            seen[hit.entity_id] = _hit_to_fields(hit, platform_id, target)
                    if not sweep.complete and sweep.stopped:
                        # a capped or stalled sweep did not cover what was
                        # asked of it, reporting "done" here is the lie the
                        # API path's `platform_status="partial"` exists to avoid
                        outcome.reason = outcome.reason or f"{keyword!r}/{tab}: {sweep.stopped}"
                    await _say(
                        on_progress,
                        f"[{platform_id}] {keyword!r}/{tab}: {len(sweep.hits or [])} hit(s), "
                        f"{len(seen)} unique so far",
                    )
            outcome.status = "partial" if (outcome.reason or outcome.attempted < units) else "done"
            break  # this credential worked; no need to try the others
        except Exception as e:
            log.error(f"[{platform_id}] sweep failed: {traceback.format_exc()}")
            outcome.status, outcome.reason = "failed", f"{type(e).__name__}: {e}"
            break
        finally:
            # Telegram holds an MTProto connection and YouTube nothing at
            # all; only some discoverers have a stop(). Both must be
            # released even when the sweep threw, or the process hangs.
            if discoverer is not None and hasattr(discoverer, "stop"):
                try:
                    await discoverer.stop()
                except Exception:
                    pass
            if session is not None:
                try:
                    await session.stop()
                except Exception:
                    pass
    else:
        # every credential was rejected, `for` completed without `break`
        if outcome.status == "failed" and not outcome.reason:
            outcome.reason = "no usable credential"

    outcome.found = len(seen)
    outcome.seconds = round(time.monotonic() - began, 2)
    return outcome, list(seen.values())


# -------------------------------------------------------------- analysis


async def analyze(
    request: AnalysisRequest,
    store: Optional[CredentialStore] = None,
    on_progress: Progress = None,
) -> EngineResult:
    """Profile URLs -> scored rows.

    URLs are grouped by platform so each platform's session is opened once
    for the whole batch, not once per profile, opening a logged-in session
    is the expensive, ban-risk-bearing part (see `stealth/`).
    """
    store = store or CredentialStore()
    started = time.monotonic()
    result = EngineResult(kind="analysis", started_at=_now_iso())

    grouped: dict[str, list[str]] = {}
    for url in request.urls:
        platform_id = request.platform or platform_for_url(url)
        if not platform_id:
            result.errors.append(f"cannot tell which platform this URL belongs to: {url}")
            continue
        grouped.setdefault(platform_id, []).append(url)

    if not grouped:
        result.errors.append("no analysable URLs given")
        result.finished_at, result.seconds = _now_iso(), time.monotonic() - started
        return result

    evidence_root: Optional[Path] = None
    if request.evidence_dir == "-":
        evidence_root = None
    elif request.evidence_dir:
        evidence_root = Path(request.evidence_dir)
    elif settings.capture_evidence:
        evidence_root = Path(settings.evidence_path)

    runnable: dict[str, list[str]] = {}
    for platform_id, urls in grouped.items():
        if platform_id not in registry.PLATFORMS:
            result.platforms.append(PlatformOutcome(platform_id, "skipped", "unknown platform", total=len(urls)))
        elif store.state_for(platform_id) != READY:
            result.platforms.append(PlatformOutcome(
                platform_id, "skipped",
                f"{store.state_for(platform_id)} -- {store.why_not(platform_id)}", total=len(urls)))
        else:
            if registry.get(platform_id).analysis_stub:
                # analysis_path always exists (every platform needs a Scraper
                # class), so it can't signal "analysis actually works", this
                # flag is the real signal, and a caller deserves the caveat
                # up front rather than after an empty run.
                result.errors.append(f"{platform_id}: analysis is a stub -- fields will come back empty")
            runnable[platform_id] = urls

    if not runnable:
        result.errors.append(
            "no platform has usable credentials -- run `python -m backend.engine platforms` to see what is missing")
        result.finished_at, result.seconds = _now_iso(), time.monotonic() - started
        return result

    await _say(on_progress, f"analysis: {sum(len(u) for u in runnable.values())} url(s) across {len(runnable)} platform(s)")

    for platform_id, urls in runnable.items():
        outcome, profiles = await _analyze_platform(
            platform_id, urls, request, evidence_root, store, on_progress)
        result.platforms.append(outcome)
        result.profiles.extend(profiles)

    result.finished_at, result.seconds = _now_iso(), time.monotonic() - started
    await _say(on_progress, result.summary())
    return result


async def _analyze_platform(
    platform_id: str, urls: list[str], request: AnalysisRequest,
    evidence_root: Optional[Path], store: CredentialStore, on_progress: Progress,
) -> tuple[PlatformOutcome, list[dict]]:
    plat = registry.get(platform_id)
    outcome = PlatformOutcome(platform_id, "failed", total=len(urls))
    began = time.monotonic()
    rows: list[dict] = []
    remaining = list(urls)

    options = ScanOptions(
        evidence=str(evidence_root) if evidence_root else None,
        headful=request.headful,
        timeout=settings.request_timeout_sec,
        delay=request.delay or settings.analysis_delay_sec,
        concurrency=request.concurrency,
    )
    target = request.target
    feed = request.official_feed

    # The outer loop rotates to a fresh credential when the current one dies
    # mid-batch, rather than failing the whole run over one bad session,
    # same shape as `analysis_service._run_one`, minus the durable backoff
    # there is nowhere to store.
    for item in store.sessions_for(platform_id):
        if not remaining:
            break
        outcome.session = item["identifier"]
        scraper = plat.scraper()(
            options, item.get("cookies", []),
            session_id=item.get("id", ""), proxy=item.get("proxy"),
        )
        await scraper.start()
        try:
            if not await scraper.check_session():
                if not plat.uses_cookies:
                    # key/MTProto-authed: there is no other credential to
                    # rotate to, the same one would just fail again forever
                    outcome.reason = "credentials invalid or rejected"
                    break
                store.burn(item["id"], "session rejected at check_session")
                outcome.reason = f"session {item['identifier']} invalid or checkpointed"
                continue

            while remaining:
                url = remaining[0]
                try:
                    row = await scraper.one(url, target, feed)
                except Exception as e:
                    err = str(e).lower()
                    if "rate limit" in err or "checkpoint" in err or "login" in err:
                        store.burn(item["id"], f"died mid-batch: {e}")
                        outcome.reason = f"session {item['identifier']} rate limited or checkpointed"
                        break  # rotate; `remaining` still holds this url
                    row = Row(url=url, target=target, original_feed=feed)
                    row.status = "ERROR"
                    row.note(f"unexpected: {type(e).__name__}: {e}")
                    log.error(f"{url} raised past .one(): {traceback.format_exc()}")

                rows.append(_row_to_fields(row, platform_id, evidence_root))
                remaining.pop(0)
                outcome.attempted += 1
                await _say(
                    on_progress,
                    f"[{platform_id}] {outcome.attempted}/{len(urls)} "
                    f"{row.profile_name or url} [{row.priority}]",
                )

                if row.status == "CHECKPOINT":
                    store.burn(item["id"], "checkpointed mid-batch")
                    outcome.reason = f"session {item['identifier']} checkpointed"
                    break
                if remaining:
                    try:
                        await scraper.pause()
                    except Exception:
                        pass
        except Exception as e:
            log.error(f"[{platform_id}] analysis failed: {traceback.format_exc()}")
            outcome.reason = f"{type(e).__name__}: {e}"
            break
        finally:
            try:
                await scraper.stop()
            except Exception:
                pass

    if remaining and not outcome.reason:
        outcome.reason = "session rotation exhausted"
    outcome.status = "done" if not remaining else "partial" if rows else "failed"
    outcome.found = len(rows)
    outcome.seconds = round(time.monotonic() - began, 2)
    return outcome, rows


# ------------------------------------------------------------------ sync


def run(coro: Awaitable[EngineResult]) -> EngineResult:
    """`asyncio.run` for callers that are not already async.

    Exists so the synchronous entry point is one obvious call rather than
    every caller rediscovering the event-loop boilerplate.
    """
    return asyncio.run(coro)  # type: ignore[arg-type]
