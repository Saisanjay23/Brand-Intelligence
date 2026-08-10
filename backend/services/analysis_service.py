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

import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.database.repositories import profile_repository as profiles_db
from backend.services import health_service as health_engine
from backend.services import incident_service as incidents_engine
from backend.sessions import manager as sessions_engine
from backend.services.job_service import Job
from backend.shared.models.row import Row
from backend.platforms.scan_options import ScanOptions
from backend.config.settings import settings
from backend.shared.logging import get_logger

log = get_logger("services.analysis")


def _tri(flag: str) -> Optional[bool]:
    """Row's "Yes"/"No"/"" -> True/False/None.

    The empty string means the scraper could not determine the field, which
    is NOT the same as determining it to be false. Collapsing both to False
    (what this used to do) published "this account is inactive" about
    profiles whose last-post date was never visible to begin with -- routine
    on Telegram and Instagram -- and dragged their risk rating down with it.
    `save()` drops None rather than writing it, so an unknown stays unknown
    instead of overwriting a value an earlier, more complete run did read.
    """
    return True if flag == "Yes" else False if flag == "No" else None


def _relative_screenshot(raw: str) -> str:
    """An absolute capture path -> the path stored on the profile document,
    relative to `settings.evidence_path`.

    Never store the absolute path: it leaks the server's filesystem layout
    through the API, and it breaks the moment the evidence volume is mounted
    somewhere else. `GET /profiles/{id}/screenshot` re-joins this against
    the configured root and refuses anything that escapes it.
    """
    if not raw:
        return ""
    try:
        return Path(raw).resolve().relative_to(settings.evidence_path.resolve()).as_posix()
    except (ValueError, OSError):
        return ""


def _row_to_fields(row: Row) -> dict:
    """An analysis Row -> the plain field dict `profile_repository.save` expects,
    carrying the scoring across."""
    shot = _relative_screenshot(row.screenshot)
    fields = {
        "url": row.url, "entity_id": row.profile_id, "keyword": "",
        "display_name": row.profile_name, "entity_type": row.entity_type,
        "target": row.target, "official_feed": row.original_feed,
        "followers": row.followers, "followers_exact": row.followers_exact,
        "friends": row.friends, "location": row.location,
        "profile_image_url": row.profile_pic_url,
        "has_logo": _tri(row.logo_yes), "verified": bool(row.verified),
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
    # ones a previous run already scored -- not just the ones still owed
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
        targets = []
        for platform_id, plat in registry.PLATFORMS.items():
            if not plat.enabled or await registry.session_state(plat) != "ready":
                continue
            urls = await profiles_db.urls_for(
                job.client_id, platform_id, "approved", exclude_analysed=exclude_analysed,
            )
            if urls:
                targets.append((platform_id, urls))

    total_urls = sum(len(urls) for _, urls in targets)
    if total_urls == 0:
        # a normal, common outcome for a batch run -- several jobs can
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
            # stopped early -- pool exhausted, credentials rejected -- is
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
    """Where this run's screenshots go, or None when capture is disabled.

    Partitioned by client and platform so a client-deletion cascade can drop
    one directory tree, and so a single directory never accumulates every
    profile the engine has ever visited.
    """
    if not settings.capture_evidence:
        return None
    safe_client = re.sub(r"[^A-Za-z0-9._-]", "_", client_id or "unknown")[:80]
    return str(settings.evidence_path / safe_client / platform_id)


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

    await mgr.emit(job, "progress", platform=platform_id, platform_status="running", platform_total=len(urls))

    while remaining:
        try:
            plat, session_item = await sessions_engine.session_for_job(platform_id)
        except Exception as e:
            log.warning(f"[{platform_id}] stopping analysis early: could not acquire session: {e}")
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
        )
        await scraper.start()
        try:
            if not await scraper.check_session():
                session_id = session_item.get("id", "")
                if not session_id:
                    # key/MTProto-authed: no pool to rotate through -- the
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

            while remaining:
                url = remaining[0]
                i = len(urls) - len(remaining) + 1
                try:
                    row = await scraper.one(url, target, feed)
                except Exception as e:
                    err_str = str(e).lower()
                    if "rate limit" in err_str or "checkpoint" in err_str or "login" in err_str:
                        log.warning(f"[{platform_id}] session {session_item.get('identifier')} died/rate limited -- rotating")
                        # Backoff is now graduated and keyed on this
                        # session's own consecutive-failure count (see
                        # sessions/manager.py::mark_session_failed) -- one
                        # 429 no longer burns a session for a full day, so
                        # a bad afternoon can't quarantine the whole pool.
                        await sessions_engine.mark_session_failed(
                            platform_id, session_item.get("id", ""), "rate_limited",
                        )
                        break  # inner loop; outer loop retries with a new session

                    if "navigation failed" in err_str or "timeout" in err_str:
                        consecutive_timeouts += 1
                        if consecutive_timeouts >= 10:
                            import asyncio
                            asyncio.create_task(incidents_engine.record(
                                platform_id, "analysis", job.client_id, job.id, "Proxy/IP Block",
                                "Too many consecutive network timeouts. The proxy pool may be exhausted or local IP blocked.",
                                url,
                            ))
                            consecutive_timeouts = 0
                    else:
                        consecutive_timeouts = 0

                    row = Row(url=url, target=target, original_feed=feed)
                    row.status = "ERROR"
                    row.note(f"unexpected: {type(e).__name__}: {e}")
                    log.error(f"job {job.id}: {url} raised past .one(): {e}\n{traceback.format_exc()}")

                rows.append(row)
                if row.status != "ERROR":
                    consecutive_timeouts = 0
                remaining.pop(0)
                attempted += 1

                # Save immediately to database so results are visible right away in UI
                try:
                    s, n = await profiles_db.save_many(
                        job.client_id, platform_id, "analysis", [_row_to_fields(row)]
                    )
                    saved += s
                    new += n
                    job.new_profiles += n
                except Exception as e:
                    log.warning(f"job {job.id}: failed to save analyzed profile {url}: {e}")

                try:
                    health_engine.record(platform_id, row.status, row.notes)
                except Exception:
                    pass
                try:
                    await mgr.emit(
                        job, "item", f"[{platform_id}] {i}/{len(urls)} {row.profile_name or url} [{row.priority}]",
                        found=i, platform=platform_id, platform_status="running", platform_processed=i,
                    )
                except Exception:
                    pass

                if row.status == "CHECKPOINT":
                    await sessions_engine.mark_session_failed(
                        platform_id, session_item.get("id", ""), "checkpointed",
                    )
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
    return saved, new, attempted, stop_reason


def _check_last_post_extraction_health(platform_id: str, job: Job, rows: list[Row]) -> None:
    """Detect the analysis-phase equivalent of discovery's parser-drift
    canary (see discovery_service.py / shared/extraction.py): a platform
    changing its page or payload shape doesn't raise an exception, it just
    makes every last-post extraction tier come up empty -- a row that loads
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
    import asyncio
    asyncio.create_task(incidents_engine.record(
        platform_id, "analysis", job.client_id, job.id, "LastPostExtractionDrift", msg,
        where=_LAST_POST_FUNCTION.get(platform_id, ""),
    ))


# The exact function an operator should open first for each platform's
# last-post extraction -- not a dynamic blame trail like discovery's
# run_strategies chain (these engines' last-post tiers are plain functions,
# not registered strategies), but naming the right function directly is
# still far better than the alert saying only "Facebook broke".
_LAST_POST_FUNCTION = {
    "facebook": "backend/platforms/facebook/analysis_engine.py: read_last_post() / dom_last_post()",
    "twitter": "backend/platforms/twitter/discovery_engine.py: latest_post() -- backend/platforms/twitter/analysis_engine.py: dom_last_post()",
    "instagram": "backend/platforms/instagram/analysis_engine.py: Scraper.read_last_post_date()",
}
