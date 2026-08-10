"""Discovery use case: a client's keywords -> candidate profiles -> Mongo.

Swept across EVERY platform that has a ready session by default (the Sweep
button's "All Platforms" choice, `job.platform is None`) -- this figures
out which platforms are usable and visits all of them in one job. A caller
may instead name exactly one platform (`job.platform` set, from
`DiscoveryIn.platform` -- the button's one-platform selector), in which
case only that platform is checked/swept and every other ready platform is
left untouched.

Saves results incrementally per completed sweep rather than batching
everything at the end, so a caller polling `GET /jobs/{id}` sees new
profiles within seconds of scraping, not only once the whole thing ends.
One platform's session dying mid-run does not abort the others -- each
platform's sweep is caught and noted independently.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Optional
from urllib.parse import urlparse

from backend.database.repositories import profile_repository as profiles_db
from backend.services import incident_service as incidents_engine
from backend.sessions import manager as sessions_engine
from backend.services.job_service import Job
from backend.platforms.scan_options import DiscoveryOptions
from backend.config.settings import settings
from backend.shared.logging import get_logger
from backend.shared.text import handle_score, name_score

log = get_logger("services.discovery")

# Each platform's discovery_engine.py only ever understands its own fixed
# tab vocabulary (see each module's `sweep(keyword, tab)`); a caller-supplied
# `tabs` list from POST /discovery used to be applied identically to every
# platform in the sweep, which meant e.g. Twitter/Instagram/YouTube -- none
# of which have a "pages" tab -- were swept for "pages" too, wasting a whole
# extra pass per keyword for nothing. Every platform now always sweeps
# exactly the tab(s) it actually supports, regardless of what was requested.
PLATFORM_TABS: dict[str, list[str]] = {
    "facebook": ["people", "pages"],
    "twitter": ["people"],
    "instagram": ["people"],
    "youtube": ["channels"],
    "telegram": ["all"],
}


def _hit_to_fields(hit, platform: str, official_handle: str = "") -> dict:
    """A discovery Hit -> the plain field dict `profile_repository.save` expects."""
    username = ""
    url = hit.url or ""
    if platform == "facebook":
        from backend.platforms.facebook.discovery_engine import profile_id
        username = profile_id(url)
    else:
        # Split host from path properly instead of taking the last "/"
        # segment of the whole string: for a URL with no path at all
        # ("https://twitter.com/") the naive version returned the HOSTNAME
        # as the username, so the profile was stored with username
        # "twitter.com" -- and any handle comparison would then be scoring
        # the platform's own domain against the brand's handle.
        # `//` is prepended when the URL has no scheme so that urlparse
        # still treats the leading token as a host rather than as path.
        parsed = urlparse(url if "//" in url else f"//{url}")
        parts = [s for s in parsed.path.split("/") if s]
        if parts:
            candidate = parts[-1].lstrip("@")
            if "?" not in candidate and "#" not in candidate:
                username = candidate
    return {
        "url": url, "entity_id": hit.entity_id, "keyword": hit.keyword,
        "username": username, "display_name": hit.name, "entity_type": hit.entity_type,
        "discovery_source": hit.source, "profile_image_url": getattr(hit, "avatar", ""),
        "has_logo": bool(getattr(hit, "has_custom_pic", False)),
        "verified": bool(getattr(hit, "verified", False)),
        # how closely the scraped name matches the keyword that found it --
        # seeds the card's High/Low match badge; analysis re-scores this
        # more precisely once a profile is actually visited.
        "name_score": name_score(hit.name or "", hit.keyword or ""),
        # How closely this profile's HANDLE matches the brand's own official
        # handle on this platform -- the second, independent impersonation
        # signal alongside name_score (which only ever looks at display
        # names). Deliberately omitted, not zeroed, when the client has no
        # official handle configured for this platform: a stored 0 would be
        # indistinguishable from "we checked and it doesn't match", and
        # anything downstream filtering on a low score would then quietly
        # act on a measurement that was never taken.
        **(
            {"username_score": handle_score(username, official_handle)}
            if official_handle and username
            else {}
        ),
    }


async def _platform_readiness(only: Optional[str] = None) -> tuple[list[str], dict[str, str]]:
    """(ready platform ids, {skipped platform id: why}) -- every enabled,
    discovery-capable platform is accounted for one way or the other, so a
    sweep can never silently drop one with zero explanation. `session_state`
    returns "missing"/"incomplete" for a platform that's never had a
    session set up, or a specific reason -- see sessions/manager.py::
    state_for -- for one that has but isn't currently usable. Previously
    this just filtered non-ready platforms out with no record of WHY, so a
    session going stale between two runs of the same client looked
    identical to that platform simply "not running" for no reason.

    `only`, when given, scopes this to exactly the one named platform
    instead of every enabled+discoverable one -- the Sweep button's
    one-platform selector. It still goes through the same ready/skipped
    accounting, so picking a platform whose session isn't ready gets the
    same clear "SKIPPED (session ...)" outcome a full sweep would have
    given it, not a silent no-op. A caller-controlled `only` value that
    isn't actually a real platform (should already have been rejected by
    discovery_controller._validated_platform before a job is ever created)
    degrades to "nothing ready" rather than raising here.
    """
    from backend.platforms import registry

    ready: list[str] = []
    skipped: dict[str, str] = {}
    if only is not None:
        items = [(only, registry.PLATFORMS[only])] if only in registry.PLATFORMS else []
    else:
        items = list(registry.PLATFORMS.items())
    for platform_id, plat in items:
        if not plat.enabled or not plat.can_discover:
            continue
        state = await registry.session_state(plat)
        if state == "ready":
            ready.append(platform_id)
        else:
            skipped[platform_id] = state
    return ready, skipped


async def run_discovery(job: Job) -> None:
    from backend.services.job_service import JobManager
    from backend.platforms import registry
    from backend.database.repositories import client_repository as clients_db

    mgr = JobManager()
    p = job.params

    if p.get("profile_ids"):
        # a targeted re-resolve of already-discovered profiles, not a fresh
        # keyword search -- see _resweep_selected's own docstring.
        await _resweep_selected(job, mgr)
        return

    keywords = [k.strip() for k in p["keywords"] if k.strip()]

    # job.platform set -> the analyst picked one specific platform from the
    # Sweep button's selector rather than "All Platforms". Scope readiness
    # to just that platform so a single-platform sweep never also visits
    # every OTHER ready platform.
    ready, skipped = await _platform_readiness(job.platform)
    if not ready:
        if job.platform:
            reason = skipped.get(job.platform, "not a known discovery-capable platform")
            raise RuntimeError(f"{job.platform}: session {reason} -- cannot sweep")
        raise RuntimeError("no platform has a ready session to sweep")

    # per-platform (and, for Facebook, per-tab) result caps saved on the
    # client (see dto/client_dto.py); a platform/tab missing here is
    # uncapped -- "scrape all".
    client = await clients_db.try_get(job.client_id)
    platform_limits = (client or {}).get("platform_limits") or {}
    platform_tab_limits = (client or {}).get("platform_tab_limits") or {}
    official_handles = (client or {}).get("official_handles") or {}

    await mgr.emit(job, "progress", f"sweeping {len(ready)} platform(s) for {len(keywords)} keyword(s)", total=len(ready))

    for platform_id in ready:
        platform_tabs = PLATFORM_TABS.get(platform_id, p.get("tabs") or ["people"])
        await mgr.emit(job, "progress", platform=platform_id, platform_status="pending",
                        platform_total=max(1, len(keywords) * len(platform_tabs)))

    total_saved = total_new = 0
    notes: list[str] = []

    for platform_id, reason in skipped.items():
        # a distinct status from "failed" on purpose -- this platform was
        # never attempted at all, which calls for a different next step
        # (fix/re-check the session) than a sweep that started and broke
        await mgr.emit(job, "progress", platform=platform_id, platform_status="skipped")
        notes.append(f"{platform_id}: SKIPPED (session {reason} -- check Sessions before re-running)")

    async def _run_one(platform_id: str) -> tuple[str, int, int, str]:
        plat = registry.get(platform_id)
        platform_tabs = PLATFORM_TABS.get(platform_id, p.get("tabs") or ["people"])
        sweep_units = max(1, len(keywords) * len(platform_tabs))
        try:
            platform_params = {
                **p,
                "max_results": platform_limits.get(platform_id, p.get("max_results", 0)),
                "official_handle": official_handles.get(platform_id, ""),
            }
            tab_limits = platform_tab_limits.get(platform_id) or {}
            saved, new, note = await _sweep_platform(job, mgr, plat, keywords, platform_tabs, platform_params, tab_limits)
            # a sweep that hit a cap or stalled did NOT cover what was asked
            # of it -- saying "done" there is the same lie the analysis side
            # used to tell (see analysis_service._run_one)
            await mgr.emit(
                job, "progress", platform=platform_id,
                platform_status="partial" if note else "done",
                platform_processed=sweep_units,
            )
            return platform_id, saved, new, note
        except Exception as e:
            log.error(f"job {job.id}: {platform_id} sweep failed: {type(e).__name__}: {e}")
            await mgr.emit(job, "progress", platform=platform_id, platform_status="failed")
            return platform_id, 0, 0, f"FAILED ({type(e).__name__}: {e})"

    results = await asyncio.gather(*(_run_one(pid) for pid in ready))
    for platform_id, saved, new, note in results:
        total_saved += saved
        total_new += new
        if note:
            notes.append(f"{platform_id}: {note}")
        await mgr.emit(
            job, "progress", f"{platform_id} done", found=total_saved,
            new_profiles=job.new_profiles,
        )

    job.message = f"{total_saved} stored, {total_new} new" + (f" -- {'; '.join(notes)}" if notes else "")


async def _resweep_selected(job: Job, mgr) -> None:
    """Re-resolves name/photo for a hand-picked set of already-discovered
    profile doc ids (`job.params["profile_ids"]`) via one page visit each --
    the exact same resolve step a normal sweep's reconciliation step uses
    (see facebook/discovery_engine.py's Discovery._resolve_missing) -- with
    no keyword search at all. Lets an analyst point a refresh at just the
    handful of cards actually still stuck on a bare numeric id/no photo
    (see the profile_url_for Pages-vs-profile.php fix this exists to
    complement) instead of waiting on, or forcing, an entire keyword
    re-sweep to reach them again.

    Facebook only for now -- it's the only platform with this kind of
    standalone "just re-resolve one already-known id" step; any other
    platform's id in the selection is simply skipped (counted, not erred),
    since discovery for every other platform gets a name/photo directly
    from its own search API with nothing left to backfill later.
    """
    from backend.platforms.facebook.discovery_engine import Discovery, profile_url_for

    ids = list(dict.fromkeys(job.params.get("profile_ids") or []))
    docs = await profiles_db.get_by_ids(job.client_id, ids)
    fb_docs = [d for d in docs if d.get("platform") == "facebook" and d.get("entity_id")]
    skipped = len(ids) - len(fb_docs)

    if not fb_docs:
        job.message = "0 refreshed" + (
            f" -- {skipped} skipped (only Facebook profiles support a targeted re-resolve)" if skipped else ""
        )
        return

    await mgr.emit(job, "progress", f"re-resolving {len(fb_docs)} profile(s)", total=len(fb_docs))

    plat_obj, session_item = await sessions_engine.session_for_job("facebook")
    session = plat_obj.session_cls()(
        DiscoveryOptions(headful=not settings.headless),
        session_item.get("cookies", []),
        session_id=session_item.get("id", ""), proxy=session_item.get("proxy"),
    )
    await session.start()
    refreshed = 0
    try:
        if not await session.check_session():
            await sessions_engine.mark_session_failed("facebook", session_item.get("id", ""), "expired")
            raise RuntimeError(f"session {session_item.get('identifier')} invalid or checkpointed")

        discoverer = Discovery(DiscoveryOptions(headful=not settings.headless), session.ctx)
        by_kind: dict[str, list[str]] = {}
        for d in fb_docs:
            by_kind.setdefault(d.get("entity_type") or "profile", []).append(d["entity_id"])

        for kind, eids in by_kind.items():
            resolved = await discoverer._resolve_missing(eids, kind)
            for eid, hit in resolved.items():
                if not hit.name and not hit.avatar:
                    continue  # still unresolved (dead/checkpointed page) -- nothing worth writing back
                fields = {
                    "display_name": hit.name, "profile_image_url": hit.avatar,
                    "has_logo": hit.has_custom_pic, "entity_type": kind,
                }
                await profiles_db.save(
                    job.client_id, "facebook", "discovery", fields,
                    url=hit.url or profile_url_for(eid, kind), entity_id=eid,
                )
                refreshed += 1
                await mgr.emit(job, "item", f"refreshed {hit.name or eid}", found=refreshed, total=len(fb_docs))
    finally:
        await session.stop()

    job.message = f"{refreshed} of {len(fb_docs)} refreshed" + (f" -- {skipped} skipped (non-Facebook)" if skipped else "")


async def _sweep_platform(
    job: Job, mgr, plat, keywords: list[str], tabs: list[str], params: dict,
    tab_limits: Optional[dict[str, int]] = None,
) -> tuple[int, int, str]:
    tab_limits = tab_limits or {}
    default_max_results = params.get("max_results", 0)

    def _options_for(cap: int) -> DiscoveryOptions:
        return DiscoveryOptions(
            concurrency=params.get("concurrency", settings.discovery_concurrency),
            max_results=cap,
            max_seconds=params.get("max_seconds", settings.discovery_max_seconds),
            headful=not settings.headless,
        )

    saved = new = 0
    completed_units = 0
    sweep_units = max(1, len(keywords) * len(tabs))
    already_saved: set[str] = set()
    all_sweeps: list = []

    await mgr.emit(job, "progress", platform=plat.id, platform_status="running", platform_total=sweep_units)

    async def _save_hits(hits: list, label: str) -> None:
        """The ONLY place this sweep writes profiles.

        `already_saved` gates the write itself, not just the counting. The
        end-of-sweep pass used to re-save every hit including the ones each
        page callback had already written moments earlier, so every
        discovered profile cost two Mongo round trips -- correct, because
        `save()` is idempotent, but twice the write load for nothing.
        """
        nonlocal saved, new
        fresh = [h for h in hits if h.entity_id and h.entity_id not in already_saved]
        if not fresh:
            return
        s, n = await profiles_db.save_many(
            job.client_id, plat.id, "discovery",
            [_hit_to_fields(h, plat.id, params.get("official_handle", "")) for h in fresh]
        )
        already_saved.update(h.entity_id for h in fresh)
        saved += s
        new += n
        job.new_profiles += n
        await mgr.emit(
            job, "item", f"[{plat.id}] {label}: {len(fresh)} profile(s) ({saved} total, {new} new)",
            found=saved, new_profiles=job.new_profiles,
        )

    async def _on_page_hits(keyword: str, tab: str, found_count: int, page_num: int, new_hits: list) -> None:
        await _save_hits(new_hits, f"{tab} {keyword!r} (page {page_num})")

    async def _on_sweep_done(sweep) -> None:
        nonlocal completed_units
        all_sweeps.append(sweep)
        # catches whatever the page callbacks never saw: the reconciliation
        # backfill, and every hit at all when the engine has no on_progress
        await _save_hits(sweep.hits or [], f"{sweep.keyword!r} done")
        completed_units += 1
        await mgr.emit(job, "progress", platform=plat.id, platform_status="running", platform_processed=completed_units)

    # Group tabs by their resolved cap so a platform with per-tab limits
    # (currently only Facebook: people vs pages) gets its own discoverer
    # instance/options per distinct cap; every other platform (one shared
    # cap across its single tab) takes exactly one group, same as before
    # this per-tab support existed.
    groups: dict[int, list[str]] = {}
    for tab in tabs:
        cap = tab_limits.get(tab, default_max_results)
        groups.setdefault(cap, []).append(tab)

    plat_obj, session_item = await sessions_engine.session_for_job(plat.id)
    if session_item.get("id"):
        # so the Sessions panel can show "currently running" against the
        # exact pooled item this sweep picked -- see
        # sessions/manager.py::_session_in_use(). Skipped for Telegram
        # (env_keys auth, session_for_job returns {} -- no pooled id to
        # highlight), harmless no-op there.
        await mgr.emit(job, "progress", platform=plat.id, session_id=session_item["id"])
    if not plat_obj.session_path:
        # No browser Session object here (that's the `else` branch's job),
        # so nothing else closes whatever connection this discoverer opens
        # for itself -- confirmed live this mattered for real: Telegram's
        # Discovery.sweep() opens its own MTProto connection lazily and,
        # without this, never closed it, leaving the local SQLite
        # `.session` file locked for the NEXT Telegram operation (an
        # analysis run for the same client, immediately after, in the
        # round-robin engine's own per-client turn) to fail against. See
        # platforms/telegram/discovery_engine.py::Discovery.stop().
        for cap, group_tabs in groups.items():
            discoverer = plat_obj.discoverer()(_options_for(cap), None)
            try:
                await _run_incremental(discoverer, keywords, group_tabs, _on_sweep_done, _on_page_hits)
            finally:
                await discoverer.stop()
    else:
        session = plat_obj.session_cls()(
            _options_for(default_max_results), session_item.get("cookies", []),
            session_id=session_item.get("id", ""), proxy=session_item.get("proxy"),
        )
        await session.start()
        try:
            if not await session.check_session():
                await sessions_engine.mark_session_failed(plat.id, session_item.get("id", ""), "expired")
                raise RuntimeError(f"session {session_item.get('identifier')} invalid or checkpointed")
            for cap, group_tabs in groups.items():
                discoverer = plat_obj.discoverer()(_options_for(cap), session.ctx)
                await _run_incremental(discoverer, keywords, group_tabs, _on_sweep_done, _on_page_hits)
        finally:
            await session.stop()

    # ── parser-drift canary ────────────────────────────────────────────
    # A platform rotating its payload shape (Facebook's GraphQL doc ids do
    # this) does not raise: the parser simply stops recognising edges,
    # `by_id` stays empty, the scroll loop exits as "stalled", and the job
    # reports 0 profiles as a clean SUCCESS. That is the worst failure mode
    # available -- silent, total, and indistinguishable from "this client
    # genuinely has no impersonators". Nothing was watching for it.
    #
    # A sweep that returned nothing at all for keywords that have
    # historically returned results is the signal. It is recorded as an
    # incident and forced into the job's own note, so it surfaces the same
    # day rather than at the next quarterly review.
    empty = [s for s in all_sweeps if not (s.hits or [])]
    note_parts: list[str] = []
    if all_sweeps and len(empty) == len(all_sweeps):
        baseline = await _historic_yield(job.client_id, plat.id)
        if baseline > 0:
            # Distinguish "the platform changed its payload" from "this
            # session is not logged in". Both produce zero results, and
            # blaming the wrong one sends an engineer to read parser code
            # for a day when the actual fix is re-exporting cookies. A
            # sweep that was refused (403/401) or bounced to a login wall
            # never got far enough to parse anything, so parser drift is
            # not a possible explanation for it.
            blocked = [
                s for s in all_sweeps
                if any(tok in (getattr(s, "stopped", "") or "").lower()
                       for tok in ("403", "401", "429", "login", "checkpoint", "auth"))
            ]
            if blocked:
                reasons = sorted({getattr(s, "stopped", "") for s in blocked})
                msg = (
                    f"{plat.id}: every one of {len(all_sweeps)} sweep(s) returned 0 results, and "
                    f"{len(blocked)} of them was refused by the platform ({', '.join(reasons)}). "
                    "This is a SESSION problem, not a scraper-code problem: the account is logged "
                    "out, challenged, or rate-limited. Re-export cookies for this platform under "
                    "Admin -> Sessions. Do not go looking for a parser bug first."
                )
                log.error(msg)
                await incidents_engine.record(
                    plat.id, "discovery", job.client_id, job.id, "SessionInvalid", msg,
                    where=_blame_trail(all_sweeps),
                )
                note_parts.append(f"SESSION REFUSED: {', '.join(reasons)} -- re-export cookies")
                return saved, new, "; ".join(note_parts)

            msg = (
                f"{plat.id}: every one of {len(all_sweeps)} sweep(s) returned 0 results, "
                f"but this client/platform has {baseline} profile(s) from previous runs. "
                "No sweep was refused by the platform, so the session is reaching the results "
                "page and finding nothing recognisable there -- the signature of the platform "
                "changing its search payload shape (rotated GraphQL doc ids, a new response "
                "envelope), not of the keywords genuinely matching nothing."
            )
            log.error(msg)
            # The blame trail from whichever engine ran a strategy chain
            # (see shared/extraction.py): the exact file, line and source
            # text of every extraction method that failed. Without this the
            # alert says "Facebook discovery broke"; with it, it says which
            # line to open.
            await incidents_engine.record(
                plat.id, "discovery", job.client_id, job.id, "ParserDrift", msg,
                where=_blame_trail(all_sweeps),
            )
            note_parts.append("SUSPECT: 0 results across every sweep despite prior history -- possible parser drift")

    # A sweep that fell through to a weaker extraction method still produced
    # results, so nothing above fires -- but the primary path is already
    # broken and will take the rest with it. Surface it now, while output
    # still looks healthy, rather than at the next total failure.
    degraded = [s for s in all_sweeps if getattr(s, "extraction", None) and s.extraction.degraded]
    if degraded:
        first = degraded[0].extraction
        msg = (
            f"{plat.id}: {len(degraded)} of {len(all_sweeps)} sweep(s) fell back to a secondary "
            f"extraction method ({first.strategy}) because the primary one stopped returning "
            "anything. Results are still flowing but with fewer fields per profile, and the "
            "primary path needs fixing before the fallback ages out too."
        )
        log.warning(msg)
        await incidents_engine.record(
            plat.id, "discovery", job.client_id, job.id, "ExtractionDegraded", msg,
            where=_blame_trail(degraded),
        )
        note_parts.append(f"DEGRADED: fell back to {first.strategy}")

    incomplete = [s for s in all_sweeps if not s.complete]
    if incomplete:
        note_parts.append(
            f"{len(incomplete)} sweep(s) INCOMPLETE: "
            + ", ".join(f"{s.keyword!r}/{s.tab} ({s.stopped})" for s in incomplete)
        )
    return saved, new, "; ".join(note_parts)


def _blame_trail(sweeps: list) -> str:
    """Merge every sweep's extraction failures into one deduplicated report.

    Deduplicated on purpose: a 6-keyword sweep whose payload parser broke
    fails at the SAME line six times, and six identical stack locations in
    an alert email buries the one fact that matters.
    """
    seen: set[str] = set()
    out: list[str] = []
    for s in sweeps:
        chain = getattr(s, "extraction", None)
        if not chain:
            continue
        for f in chain.failures:
            key = f"{f.strategy}|{f.file}:{f.line}"
            if key in seen:
                continue
            seen.add(key)
            out.append(f"  {f.describe()}")
    return "\n".join(out)


async def _historic_yield(client_id: str, platform_id: str) -> int:
    """How many profiles this client/platform pair has ever produced.

    The baseline the drift canary compares against: zero results is only
    suspicious where results have existed before. A brand-new client
    legitimately returns nothing on its first sweep and must not be
    reported as a broken parser.
    """
    try:
        stats = await profiles_db.stats(client_id, platform_id)
        return int(stats.get("total") or 0)
    except Exception:
        return 0


async def _run_incremental(discoverer, keywords: list[str], tabs: list[str], on_sweep_done, on_page_hits=None) -> list:
    """Run sweeps at the discoverer's own concurrency, calling
    on_sweep_done for each completed sweep immediately instead of waiting
    for gather, and on_page_hits for every batch of new results found
    mid-sweep."""
    jobs = [(k, t) for k in keywords for t in tabs]
    concurrency = getattr(discoverer, "a", None)
    max_conc = getattr(concurrency, "concurrency", 2) if concurrency else 2
    sem = asyncio.Semaphore(max(1, max_conc))

    async def one(i: int, keyword: str, tab: str):
        async with sem:
            await asyncio.sleep(i % max(1, max_conc) * 1.0)

            async def _progress(found_count: int, page_num: int, new_hits: list) -> None:
                if not on_page_hits:
                    return
                try:
                    await on_page_hits(keyword, tab, found_count, page_num, new_hits)
                except Exception:
                    pass

            sig = inspect.signature(discoverer.sweep)
            if "on_progress" in sig.parameters:
                sweep = await discoverer.sweep(keyword, tab, on_progress=_progress)
            else:
                sweep = await discoverer.sweep(keyword, tab)
            await on_sweep_done(sweep)
            return sweep

    return list(await asyncio.gather(*(one(i, k, t) for i, (k, t) in enumerate(jobs))))
