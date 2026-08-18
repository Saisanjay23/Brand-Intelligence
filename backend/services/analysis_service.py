"""Analysis use case: approved profile URLs -> scored rows -> Mongo.

`job.platform` is either one platform (the approve -> auto-trigger path
batches by the just-approved profile's own platform) or None (the manual
catch-up trigger, which analyses every approved-but-unanalysed profile for
the client across every platform with a ready session).

The outer retry loop rotates to a fresh session from the pool whenever the
current one dies mid-batch (rate limit, checkpoint, or a rejected
check_session()) rather than failing the whole job over one bad session.
For a key/MTProto-authed platform there is no other session to rotate to,
so a failed check there fails the job outright instead of looping forever.
"""

from __future__ import annotations

import asyncio
import re
import traceback
from datetime import datetime, timezone
from typing import Optional

from backend.database.repositories import profile_repository as profiles_db
from backend.services import health_service as health_engine
from backend.services import incident_service as incidents_engine
from backend.sessions import manager as sessions_engine
from backend.services.job_service import Job
from backend.shared.extraction import locate
from backend.shared.models.row import Row
from backend.shared.resilience import classify_failure, is_transient, retry_async
from backend.platforms.scan_options import ScanOptions
from backend.config.settings import settings
from backend.shared.logging import get_logger

log = get_logger("services.analysis")


def _tri(flag: str) -> Optional[bool]:
    """Row's "Yes"/"No"/"" -> True/False/None.

    The empty string means the scraper could not determine the field, which
    is NOT the same as determining it to be false. Collapsing both to False
    (what this used to do) published "this account is inactive" about
    profiles whose last-post date was never visible to begin with, routine
    on Telegram and Instagram, and dragged their risk rating down with it.
    `save()` drops None rather than writing it, so an unknown stays unknown
    instead of overwriting a value an earlier, more complete run did read.
    """
    return True if flag == "Yes" else False if flag == "No" else None


def _row_to_fields(row: Row) -> dict:
    """An analysis Row -> the plain field dict `profile_repository.save` expects,
    carrying the scoring across."""
    # `row.screenshot` is already the exact GridFS key a platform's own
    # screenshot() method wrote the capture under (see evidence_repository.py)
    #; nothing left to resolve or validate against a filesystem root here,
    # since a GridFS key is just a string lookup, not a path that can escape
    # anywhere.
    shot = (row.screenshot or "").strip()
    fields = {
        "url": row.url, "entity_id": row.profile_id, "keyword": "",
        "display_name": row.profile_name, "entity_type": row.entity_type,
        "target": row.target, "official_feed": row.original_feed,
        "followers": row.followers, "followers_exact": row.followers_exact,
        "friends": row.friends, "location": row.location,
        "profile_image_url": row.profile_pic_url,
        # row.verified is None when this platform's analysis engine never
        # checked for a badge at all (see shared/models/row.py), passed
        # through AS-IS, not coerced with bool(), so save()'s existing
        # None-drops-the-key filter leaves an already-True value (set by
        # discovery, or an earlier analysis pass) alone instead of every
        # re-analysis silently stomping it back to False. bool(row.verified)
        # used to do exactly that for any platform whose analysis phase
        # doesn't explicitly detect a badge.
        "has_logo": _tri(row.logo_yes), "verified": row.verified,
        "is_active": _tri(row.active_yes),
        "has_name_match": _tri(row.name_yes), "name_score": row.name_score,
        "last_post_date": row.last_post_iso, "risk_score": row.risk, "priority": row.priority,
        "comments": row.notes, "analysis_status": row.status, "sources": dict(row.src),
    }
    if shot:
        fields["screenshot"] = shot
        fields["screenshot_at"] = datetime.now(timezone.utc)
    return fields


async def run_analysis(job: Job) -> None:
    from backend.services.job_service import JobManager

    mgr = JobManager()
    p = job.params
    # force=True: re-analyse every currently-approved profile, including
    # ones a previous run already scored, not just the ones still owed
    # (unanalysed, or failed-and-retryable). This is what makes the
    # analyst-facing "run analysis again" button actually run again, even
    # when the auto-trigger-on-approve or the 20-minute catch-up sweep has
    # already cleared the normal backlog to zero.
    force = bool(p.get("force"))
    exclude_analysed = not force

    if job.platform:
        targets = [(job.platform, await profiles_db.urls_for(
            job.client_id, job.platform, "approved", exclude_analysed=exclude_analysed,
        ))]
    else:
        from backend.platforms import registry
        # The Run hub's multi-select (see discovery_controller
        # ::_resolve_platforms). Absent means every ready platform, which
        # is the long-standing "All Platforms" behaviour.
        wanted = [str(x) for x in (p.get("platforms") or []) if str(x).strip()]
        targets = []
        for platform_id, plat in registry.PLATFORMS.items():
            if wanted and platform_id not in wanted:
                continue
            if not plat.enabled or await registry.session_state(plat) != "ready":
                continue
            urls = await profiles_db.urls_for(
                job.client_id, platform_id, "approved", exclude_analysed=exclude_analysed,
            )
            if urls:
                targets.append((platform_id, urls))

    total_urls = sum(len(urls) for _, urls in targets)
    if total_urls == 0:
        # a normal, common outcome for a batch run, several jobs can
        # queue behind a platform's lock at once, and by the time a later
        # one runs the first may have already covered everything. Not a
        # real failure: must not raise/FAILED/alert. force=True reaching
        # here means the client simply has no approved profiles at all on
        # any ready platform, not that the run was skipped.
        job.message = "nothing to analyse -- already up to date" if not force else "nothing to analyse -- no approved profiles on any ready platform"
        return

    await mgr.emit(
        job, "progress",
        f"{total_urls} url(s) across {len(targets)} platform(s)" + (" (re-analysing all approved)" if force else ""),
        total=total_urls,
    )

    for platform_id, urls in targets:
        await mgr.emit(job, "progress", platform=platform_id, platform_status="pending", platform_total=len(urls))

    import asyncio

    async def _run_one(platform_id: str, urls: list[str]) -> tuple[int, int, str]:
        try:
            saved, new, attempted, reason = await _analyse_platform(job, mgr, platform_id, urls, p)
            # "done" means every URL was actually attempted. A run that
            # stopped early, pool exhausted, credentials rejected, is
            # `partial`, and says why. Reporting it as done (which is what
            # happened before: the wrapper stamped processed=len(urls) on
            # any non-exception return, including the early `break`) told an
            # analyst 200/200 when 12 profiles had been visited, and there
            # was nothing anywhere to contradict it.
            complete = attempted >= len(urls)
            await mgr.emit(
                job, "progress", platform=platform_id,
                platform_status="done" if complete else "partial",
                platform_processed=attempted,
            )
            note = "" if complete else f"{attempted}/{len(urls)} analysed -- stopped early ({reason})"
            return saved, new, note
        except Exception as e:
            log.error(f"job {job.id}: {platform_id} analysis failed: {type(e).__name__}: {e}")
            await mgr.emit(job, "progress", platform=platform_id, platform_status="failed")
            return 0, 0, f"FAILED ({type(e).__name__}: {e})"

    results = await asyncio.gather(*(_run_one(pid, urls) for pid, urls in targets))
    grand_saved = sum(r[0] for r in results)
    grand_new = sum(r[1] for r in results)
    notes = [f"{pid}: {note}" for (pid, _), (_, _, note) in zip(targets, results) if note]

    job.new_profiles = grand_new
    job.message = f"{grand_saved} analysed, {grand_new} new" + (f" -- {'; '.join(notes)}" if notes else "")


def _evidence_dir(client_id: str, platform_id: str) -> Optional[str]:
    """The GridFS key prefix this run's screenshots are stored under (see
    `database/repositories/evidence_repository.py`), or None when capture is
    disabled.

    Partitioned by client and platform so a client-deletion cascade can drop
    every one of that client's captures by key prefix, and so two clients'
    identically-shaped entity ids can never collide on the same key.
    """
    if not settings.capture_evidence:
        return None
    safe_client = re.sub(r"[^A-Za-z0-9._-]", "_", client_id or "unknown")[:80]
    return f"{safe_client}/{platform_id}"


async def _analyse_platform(
    job: Job, mgr, platform_id: str, urls: list[str], params: dict,
) -> tuple[int, int, int, str]:
    """-> (saved, newly-seen, urls actually attempted, why-it-stopped).

    The attempted count is what makes an honest progress report possible:
    the caller can only distinguish "finished" from "gave up" if this says
    how far it got, and returning normally after an early break made those
    two indistinguishable.
    """
    options = ScanOptions(
        # Evidence capture is what makes a finding actionable downstream:
        # the impersonating account is usually gone by the time a takedown
        # request is read, so the screenshot is often the only surviving
        # proof it existed. This was hard-coded to None, so `Row.screenshot`
        # and every engine's screenshot() were dead code.
        evidence=_evidence_dir(job.client_id, platform_id),
        delay=params.get("delay", settings.analysis_delay_sec),
        concurrency=params.get("concurrency", settings.analysis_concurrency),
        headful=not settings.headless,
    )
    target, feed = params.get("target", ""), params.get("feed", "")

    remaining = urls.copy()
    rows: list[Row] = []
    consecutive_timeouts = 0
    saved = new = attempted = 0
    stop_reason = ""

    # `platform_pending_seed` is what an analyst watching Live Activity sees
    # as "still to analyse" for this platform before a single URL completes
    await mgr.emit(
        job, "progress", platform=platform_id, platform_status="running", platform_total=len(urls),
        platform_pending_seed=list(urls),
    )

    while remaining:
        # A platform whose fields do not need a login analyses anonymously
        # when the pool has nothing healthy, instead of stopping the run.
        # Same trade discovery makes: one field (the exact last-post date,
        # which needs the profile's video grid) rather than the whole
        # platform. See Platform.anonymous_context_path.
        #
        # `session_item` stays {} on this path, which the rest of the loop
        # already tolerates -- mark_session_ok/mark_session_failed both
        # no-op on an empty session id, and check_session() returns True
        # for an anonymous scraper because there is nothing to validate
        # and nothing to quarantine.
        from backend.platforms import registry as _registry

        anon_plat = _registry.get(platform_id)
        run_anonymously = (
            anon_plat.can_run_anonymously
            and await _registry.session_state(anon_plat) != "ready"
        )
        if run_anonymously:
            log.info(f"[{platform_id}] no healthy session -- analysing anonymously")
            plat, session_item = anon_plat, {}
        else:
            try:
                plat, session_item = await sessions_engine.session_for_job(platform_id)
            except Exception as e:
                log.warning(
                    f"[{platform_id}] stopping analysis early: could not acquire session: {e}")
                stop_reason = "no healthy session remaining"
                await mgr.emit(job, "progress", f"[{platform_id}] stopped early: {stop_reason}")
                await incidents_engine.record(
                    platform_id, "analysis", job.client_id, job.id, "PoolExhausted",
                    f"Analysis stopped after {attempted}/{len(urls)} profiles: {e}",
                )
                break

        scraper = plat.scraper()(
            options, session_item.get("cookies", []),
            session_id=session_item.get("id", ""), proxy=session_item.get("proxy"),
            **({"anonymous": True} if run_anonymously else {}),
        )
        await scraper.start()
        try:
            if not await scraper.check_session():
                session_id = session_item.get("id", "")
                if not session_id:
                    # key/MTProto-authed: no pool to rotate through; the
                    # same credential would just fail again forever
                    log.warning(f"[{platform_id}] credentials invalid or rejected")
                    stop_reason = "credentials invalid or rejected"
                    break
                await sessions_engine.mark_session_failed(platform_id, session_id, "expired")
                continue  # retry loop with the next available pooled session

            await mgr.emit(
                job, "progress", f"[{platform_id}] session {session_item.get('identifier')} valid",
                total=len(urls), platform=platform_id, session_id=session_item.get("id", ""),
            )
            # a passing check_session is proof this session works right now:
            # clear any leftover quarantine and reset its consecutive-failure
            # ladder, so an account that recovers isn't still carrying the
            # backoff level it earned during an earlier bad patch
            await sessions_engine.mark_session_ok(platform_id, session_item.get("id", ""))

            # Telegram allows exactly one MTProto connection/session file at
            # a time (see sessions/manager.py::session_for_job); no
            # exceptions, regardless of what a caller configured. Every
            # other platform's `.one()` opens its own fresh page from the
            # shared browser context per call (confirmed across facebook/
            # instagram/twitter/tiktok analysis_engine.py), so a handful of
            # tabs can run against the same logged-in session safely. Capped
            # at 3 in code, not just by convention, so a bad config value
            # can never turn this into an unbounded stampede.
            wave_size = 1 if platform_id == "telegram" else max(1, min(options.concurrency, 3))
            STAGGER_SECONDS = 1.5

            async def _process_one(idx: int, url: str) -> tuple[str, Optional[Row], Optional[Exception]]:
                if idx:
                    # Tabs open a beat apart rather than all at once; the
                    # same stagger facebook/analysis_engine.py::run_parallel
                    # already uses for its own (otherwise-unreachable from
                    # this caller) multi-tab path.
                    await asyncio.sleep(idx * STAGGER_SECONDS)
                try:
                    # A same-session blip (one dropped nav, a slow XHR) gets
                    # one quick retry before counting as a real failure
                    # `retryable=is_transient` means anything session-shaped
                    # (rate limit/checkpoint/auth) skips the retry entirely
                    # and falls straight to the except below, unretried,
                    # exactly as before this existed.
                    row = await retry_async(
                        lambda: scraper.one(url, target, feed),
                        attempts=2, base_delay=2.0, max_delay=6.0,
                        retryable=is_transient,
                    )
                    return url, row, None
                except Exception as e:
                    return url, None, e

            while remaining:
                batch = remaining[:wave_size]
                results = await asyncio.gather(*(_process_one(idx, u) for idx, u in enumerate(batch)))

                # A session-shaped failure anywhere in the wave stops the
                # NEXT wave from being dispatched, but every tab already
                # in flight for THIS wave already ran by the time `gather`
                # returns, same tradeoff facebook/analysis_engine.py::
                # run_parallel already makes; there is no cheaper way to
                # abort a sibling tab mid-flight. `session_burned` dedupes
                # mark_session_failed to one call even if several tabs in
                # the same wave hit it simultaneously.
                stop_wave = False
                session_burned = False

                for url, row, e in results:
                    if e is not None:
                        reason = classify_failure(e)
                        if reason:
                            if not session_burned:
                                log.warning(f"[{platform_id}] session {session_item.get('identifier')} {reason} -- rotating")
                                # Backoff is now graduated and keyed on this
                                # session's own consecutive-failure count (see
                                # sessions/manager.py::mark_session_failed)
                                # one 429 no longer burns a session for a full
                                # day, so a bad afternoon can't quarantine the
                                # whole pool. `reason` (checkpointed/
                                # rate_limited/expired) is the SAME classifier
                                # discovery uses, instead of every one of
                                # these three shapes previously being forced
                                # into "rate_limited" regardless, a real
                                # checkpoint never reached DEAD_STATES or
                                # fired a SessionInvalid incident under the
                                # old blanket reason, so it looked like
                                # ordinary cooldown instead of an account that
                                # needs fresh cookies.
                                await sessions_engine.mark_session_failed(
                                    platform_id, session_item.get("id", ""), reason, detail=str(e),
                                )
                                session_burned = True
                            stop_wave = True
                            # left in `remaining` on purpose; the next
                            # session rotation retries it, exactly as before
                            continue

                        if is_transient(e):
                            consecutive_timeouts += 1
                            if consecutive_timeouts >= 10:
                                asyncio.create_task(incidents_engine.record(
                                    platform_id, "analysis", job.client_id, job.id, "Proxy/IP Block",
                                    "Too many consecutive network timeouts even after retrying each one. "
                                    "The proxy pool may be exhausted or local IP blocked.",
                                    url,
                                ))
                                consecutive_timeouts = 0
                        else:
                            consecutive_timeouts = 0

                        row = Row(url=url, target=target, original_feed=feed)
                        row.status = "ERROR"
                        row.note(f"unexpected: {type(e).__name__}: {e}")
                        log.error(f"job {job.id}: {url} raised past .one() (after retry if transient): {e}\n{traceback.format_exc()}")

                    rows.append(row)
                    if row.status != "ERROR":
                        consecutive_timeouts = 0
                    remaining.remove(url)
                    attempted += 1
                    i = attempted

                    # Save immediately to database so results are visible right away in UI
                    try:
                        s, n = await profiles_db.save_many(
                            job.client_id, platform_id, "analysis", [_row_to_fields(row)]
                        )
                        saved += s
                        new += n
                        job.new_profiles += n
                    except Exception as e2:
                        log.warning(f"job {job.id}: failed to save analyzed profile {url}: {e2}")

                    try:
                        health_engine.record(platform_id, row.status, row.notes)
                    except Exception:
                        pass
                    try:
                        await mgr.emit(
                            job, "item", f"[{platform_id}] {i}/{len(urls)} {row.profile_name or url} [{row.priority}]",
                            found=i, platform=platform_id, platform_status="running", platform_processed=i,
                            platform_item_done=url,
                        )
                    except Exception:
                        pass

                    if row.status == "CHECKPOINT":
                        if not session_burned:
                            await sessions_engine.mark_session_failed(
                                platform_id, session_item.get("id", ""), "checkpointed",
                            )
                            session_burned = True
                        stop_wave = True

                if stop_wave:
                    break  # inner loop; outer loop retries with a new session

                if remaining:
                    try:
                        await scraper.pause()
                    except Exception:
                        pass
        finally:
            await scraper.stop()

    if remaining and not stop_reason:
        stop_reason = "session rotation exhausted"
    _check_last_post_extraction_health(platform_id, job, rows)
    await _check_field_extraction_health(platform_id, job, rows, mgr)
    return saved, new, attempted, stop_reason


# --- Field-level extraction canaries ------------------------------------
# Generalizes _check_last_post_extraction_health (below) to any field that
# should reliably populate on a row that loaded far enough to reach status
# "OK". A platform reshaping its profile payload/DOM doesn't raise; it just
# makes one specific field come up blank while everything else keeps
# working -- the same silent-drift shape last_post already guards against,
# now for the display name and follower count too.
#
# The targets are "module:qualname", resolved to a real file:line at alert
# time by shared/extraction.py::locate(). They are NOT written as literal
# line numbers on purpose: any edit above one of these functions shifts it,
# and an alert pointing at the wrong line costs the reader more time than
# one pointing at no line at all. Each name below was taken from that
# engine's actual `row.profile_name = ` / `row.followers = ` assignment
# site, and tests_unit/test_field_canary.py asserts every one still
# resolves, so a rename fails a test instead of shipping a dead pointer.
# Fields where 0 is a real, extracted value and only None means "never
# read" -- as opposed to text fields, where an empty string is the blank.
_NUMERIC_FIELDS = frozenset({"followers", "friends"})

# Each entry is (field, label, targets). `field` may be a tuple of
# alternatives, meaning the platform publishes ONE of them per profile and
# the canary should only fire when none of them came back.
_FIELD_CANARIES: dict[str, tuple[tuple[object, str, tuple[str, ...]], ...]] = {
    "twitter": (
        ("profile_name", "display name",
         ("backend.platforms.twitter.analysis_engine:Scraper.fill",)),
        ("followers", "follower count",
         ("backend.platforms.twitter.analysis_engine:Scraper.fill",)),
    ),
    "instagram": (
        ("profile_name", "display name",
         ("backend.platforms.instagram.analysis_engine:Scraper.fill",
          "backend.platforms.instagram.analysis_engine:Scraper.fill_from_dom")),
        ("followers", "follower count",
         ("backend.platforms.instagram.analysis_engine:Scraper.fill",
          "backend.platforms.instagram.analysis_engine:Scraper.fill_from_dom")),
    ),
    "facebook": (
        ("profile_name", "display name",
         ("backend.platforms.facebook.analysis_engine:read_name",)),
        # Followers OR friends: a Page publishes one, a personal profile
        # the other, and a creator profile both. Checking `followers`
        # alone fired a critical "follower count stopped extracting"
        # alert on 14 of 18 profiles that had all published a friend
        # count perfectly well -- an alert that is wrong most of the time
        # trains everyone to ignore the ones that are right.
        (("followers", "friends"), "follower/friend count",
         ("backend.platforms.facebook.analysis_engine:read_counts",)),
    ),
    "youtube": (
        ("profile_name", "channel name",
         ("backend.platforms.youtube.analysis_engine:Scraper.fill",)),
        ("followers", "subscriber count",
         ("backend.platforms.youtube.analysis_engine:Scraper.fill",)),
    ),
    "tiktok": (
        ("profile_name", "display name",
         ("backend.platforms.tiktok.analysis_engine:Scraper.fill",
          "backend.platforms.tiktok.analysis_engine:Scraper.fill_from_dom")),
        ("followers", "follower count",
         ("backend.platforms.tiktok.analysis_engine:Scraper.fill",
          "backend.platforms.tiktok.analysis_engine:Scraper.fill_from_dom")),
    ),
    "telegram": (
        ("profile_name", "channel/group title",
         ("backend.platforms.telegram.analysis_engine:Scraper.fill",)),
        ("followers", "member count",
         ("backend.platforms.telegram.analysis_engine:Scraper.fill",)),
    ),
}

# Enough of a batch that a blank field means the parser, not the profile.
# A lone-profile catch-up run for one client would otherwise trip on one
# genuinely-odd account; these mirror the last-post canary's thresholds so
# both detectors have the same, already-live-validated sensitivity.
_CANARY_MIN_ROWS = 5
_CANARY_MIN_BLANK = 3
_CANARY_BLANK_RATIO = 0.5


def _field_blank(row: Row, field: str) -> bool:
    """Blank means "nothing was extracted", never "the real value is 0".

    `followers` is an Optional[int] where 0 is a legitimate reading for a
    brand-new account, so only None counts; treating 0 as missing would
    fire this canary on every genuinely-empty profile. Mirrors the same
    distinction shared/extraction.py::_default_is_empty draws.
    """
    # A field may be published under more than one name, in which case the
    # extraction only failed if NONE of them came back. Facebook is the
    # case that forced this: a Page publishes a follower count, a personal
    # profile publishes a friend count instead (see
    # facebook/analysis_engine.py::take_chip, which says exactly that), so
    # judging Facebook on `followers` alone declared a batch of personal
    # profiles broken while `friends` had been read from every one of them.
    fields = (field,) if isinstance(field, str) else tuple(field)
    return all(_one_field_blank(row, f) for f in fields)


def _one_field_blank(row: Row, field: str) -> bool:
    val = getattr(row, field, None)
    return val is None if field in _NUMERIC_FIELDS else not str(val or "").strip()


async def _check_field_extraction_health(platform_id: str, job: Job, rows: list[Row], mgr) -> None:
    """Per-field version of the last-post canary below.

    Routes through the same incident path, so severity, de-duplication and
    email throttling are decided in one place (services/alert_policy.py)
    rather than here. Also pushes a line into this job's own live event
    feed, so the Live Activity panel shows the break as it happens instead
    of only once the email arrives.
    """
    ok_rows = [r for r in rows if r.status == "OK"]
    if len(ok_rows) < _CANARY_MIN_ROWS:
        return
    for field, label, targets in _FIELD_CANARIES.get(platform_id, ()):
        blank = [r for r in ok_rows if _field_blank(r, field)]
        if len(blank) < _CANARY_MIN_BLANK or len(blank) / len(ok_rows) < _CANARY_BLANK_RATIO:
            continue
        where = "\n".join(f"  {locate(t)}" for t in targets)
        examples = ", ".join(r.url for r in blank[:3])
        msg = (
            f"{platform_id}: the '{label}' field came back empty for {len(blank)} of "
            f"{len(ok_rows)} profiles that loaded successfully in this batch. Those profiles "
            f"opened fine and their other fields populated, so this is not a session, network "
            f"or access problem -- it is {platform_id} having changed the part of its page or "
            f"data feed that carries '{label}'. Every profile analysed from now on will be "
            f"missing this field until the extraction code is updated. "
            f"Example profiles: {examples}"
        )
        log.error(msg)
        try:
            await mgr.emit(
                job, "progress", platform=platform_id,
                message=f"[{platform_id}] BROKEN: '{label}' no longer extracting "
                        f"({len(blank)}/{len(ok_rows)} profiles) -- see {targets[0].split(':')[-1]}()",
            )
        except Exception:
            # a live-feed line is presentation; never let it stop the
            # incident below from being recorded
            pass
        # Awaited, not fire-and-forget: this runs at the very end of a
        # platform's batch, and a bare create_task here can be collected or
        # cancelled when the job finishes -- losing exactly the alert this
        # whole detector exists to send.
        await incidents_engine.record(
            platform_id, "analysis", job.client_id, job.id, "FieldExtractionDrift", msg,
            where=where,
        )


def _check_last_post_extraction_health(platform_id: str, job: Job, rows: list[Row]) -> None:
    """Detect the analysis-phase equivalent of discovery's parser-drift
    canary (see discovery_service.py / shared/extraction.py): a platform
    changing its page or payload shape doesn't raise an exception, it just
    makes every last-post extraction tier come up empty, a row that loads
    fine (status OK) but is neither "has posts, dated X" nor "confirmed no
    posts", it is simply unknown. One or two of those on a real run is
    normal (a slow XHR, an odd profile); a majority of an OK batch is the
    signature of the platform having moved and every tier missing it the
    same way, which is worth an operator's attention before the next 400-
    client lap wastes its results the same way.

    Only fires with enough rows to mean something (a lone-profile catch-up
    run for one client would otherwise trip this on one legitimately hard
    profile) and via the same debounced incident/email path the rest of
    this file already uses, so this can't flood an inbox even on an
    engine-wide outage.
    """
    ok_rows = [r for r in rows if r.status == "OK"]
    if len(ok_rows) < 5:
        return
    blank = [r for r in ok_rows if r.posts_seen not in ("no", "yes")]
    if len(blank) < 3 or len(blank) / len(ok_rows) < 0.5:
        return
    examples = ", ".join(r.url for r in blank[:3])
    msg = (
        f"{platform_id}: {len(blank)} of {len(ok_rows)} successfully-loaded profiles in this "
        f"batch have no last-post date AND no confirmed 'no posts' signal either -- every "
        f"extraction tier came up empty, not just the preferred one. These profiles loaded "
        f"and other fields (name, followers) came through fine, so this isn't a session "
        f"problem; it is the signature of {platform_id} having changed its post/timeline "
        f"page or payload shape in a way none of the current tiers recognise. "
        f"Examples: {examples}"
    )
    log.error(msg)
    task = asyncio.create_task(incidents_engine.record(
        platform_id, "analysis", job.client_id, job.id, "LastPostExtractionDrift", msg,
        where=_last_post_where(platform_id),
    ))
    # asyncio holds only a WEAK reference to a running task: a bare
    # create_task whose result nobody keeps can be garbage-collected
    # mid-flight, which would silently drop the very incident this
    # detector exists to raise. Keeping the reference until it completes
    # is the documented way to avoid that.
    _PENDING_INCIDENTS.add(task)
    task.add_done_callback(_PENDING_INCIDENTS.discard)


_PENDING_INCIDENTS: set = set()


# The exact function an operator should open first for each platform's
# last-post extraction. Not a dynamic blame trail like discovery's
# run_strategies chain (these engines' last-post tiers are plain functions,
# not registered strategies) -- but resolved to a real file:line at alert
# time by shared/extraction.py::locate(), so the pointer stays correct as
# these files change instead of rotting into a wrong line number.
_LAST_POST_TARGETS: dict[str, tuple[str, ...]] = {
    "facebook": (
        "backend.platforms.facebook.analysis_engine:read_last_post",
        "backend.platforms.facebook.analysis_engine:dom_last_post",
    ),
    "twitter": (
        "backend.platforms.twitter.discovery_engine:latest_post",
        "backend.platforms.twitter.analysis_engine:dom_last_post",
    ),
    "instagram": (
        "backend.platforms.instagram.analysis_engine:Scraper.read_last_post_date",
    ),
    # TikTok reads the last-post date inline rather than in a dedicated
    # function (it works around a CAPTCHA on the profile's own video grid
    # by searching the username on the Videos tab instead), so the whole
    # visit method is the honest pointer here.
    "tiktok": (
        "backend.platforms.tiktok.analysis_engine:Scraper.process",
    ),
    "youtube": (
        "backend.platforms.youtube.analysis_engine:Scraper.fill",
    ),
}


def _last_post_where(platform_id: str) -> str:
    return "\n".join(f"  {locate(t)}" for t in _LAST_POST_TARGETS.get(platform_id, ()))
