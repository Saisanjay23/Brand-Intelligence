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

import time
import traceback

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


def _row_to_fields(row: Row) -> dict:
    """An analysis Row -> the plain field dict `profile_repository.save` expects,
    carrying the scoring across."""
    return {
        "url": row.url, "entity_id": row.profile_id, "keyword": "",
        "display_name": row.profile_name, "entity_type": row.entity_type,
        "target": row.target, "official_feed": row.original_feed,
        "followers": row.followers, "followers_exact": row.followers_exact,
        "friends": row.friends, "location": row.location,
        "profile_image_url": row.profile_pic_url,
        "has_logo": row.logo_yes == "Yes", "is_active": row.active_yes == "Yes",
        "has_name_match": row.name_yes == "Yes", "name_score": row.name_score,
        "last_post_date": row.last_post_iso, "risk_score": row.risk, "priority": row.priority,
        "comments": row.notes, "analysis_status": row.status, "sources": dict(row.src),
    }


async def run_analysis(job: Job) -> None:
    from backend.services.job_service import JobManager

    mgr = JobManager()
    p = job.params

    if job.platform:
        targets = [(job.platform, await profiles_db.urls_for(job.client_id, job.platform, "approved", exclude_analysed=True))]
    else:
        from backend.platforms import registry
        targets = []
        for platform_id, plat in registry.PLATFORMS.items():
            if not plat.enabled or await registry.session_state(plat) != "ready":
                continue
            urls = await profiles_db.urls_for(job.client_id, platform_id, "approved", exclude_analysed=True)
            if urls:
                targets.append((platform_id, urls))

    total_urls = sum(len(urls) for _, urls in targets)
    if total_urls == 0:
        # a normal, common outcome for a batch run -- several jobs can
        # queue behind a platform's lock at once, and by the time a later
        # one runs the first may have already covered everything. Not a
        # real failure: must not raise/FAILED/alert.
        job.message = "nothing to analyse -- already up to date"
        return

    await mgr.emit(job, "progress", f"{total_urls} url(s) across {len(targets)} platform(s)", total=total_urls)

    # Every targeted platform is queued up front, so the UI can show all of
    # them (pending -> running -> done) even though they run one at a time
    # below, not concurrently.
    for platform_id, urls in targets:
        await mgr.emit(job, "progress", platform=platform_id, platform_status="pending", platform_total=len(urls))

    grand_saved = grand_new = 0
    for platform_id, urls in targets:
        saved, new = await _analyse_platform(job, mgr, platform_id, urls, p)
        grand_saved += saved
        grand_new += new
        await mgr.emit(job, "progress", platform=platform_id, platform_status="done", platform_processed=len(urls))

    job.new_profiles = grand_new
    job.message = job.message or f"{grand_saved} analysed, {grand_new} new"


async def _analyse_platform(job: Job, mgr, platform_id: str, urls: list[str], params: dict) -> tuple[int, int]:
    options = ScanOptions(
        evidence=None, delay=params.get("delay", settings.analysis_delay_sec),
        concurrency=params.get("concurrency", settings.analysis_concurrency),
        headful=not settings.headless,
    )
    target, feed = params.get("target", ""), params.get("feed", "")

    remaining = urls.copy()
    rows: list[Row] = []
    consecutive_timeouts = 0

    await mgr.emit(job, "progress", platform=platform_id, platform_status="running", platform_total=len(urls))

    while remaining:
        plat, session_item = await sessions_engine.session_for_job(platform_id)
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
                    raise RuntimeError(f"{platform_id} credentials invalid or rejected")
                await sessions_engine.mark_session_failed(platform_id, session_id, "expired")
                continue  # retry loop with the next available pooled session

            await mgr.emit(job, "progress", f"[{platform_id}] session {session_item.get('identifier')} valid", total=len(urls))

            while remaining:
                url = remaining[0]
                i = len(urls) - len(remaining) + 1
                try:
                    row = await scraper.one(url, target, feed)
                except Exception as e:
                    err_str = str(e).lower()
                    if "rate limit" in err_str or "checkpoint" in err_str or "login" in err_str:
                        log.warning(f"[{platform_id}] session {session_item.get('identifier')} died/rate limited -- rotating")
                        await sessions_engine.mark_session_failed(
                            platform_id, session_item.get("id", ""), "rate_limited",
                            rate_limited_until=time.time() + 86400,
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
                        platform_id, session_item.get("id", ""), "checkpointed", rate_limited_until=time.time() + 86400,
                    )
                    break  # inner loop; outer loop retries with a new session

                if remaining:
                    try:
                        await scraper.pause()
                    except Exception:
                        pass
        finally:
            await scraper.stop()

    if not rows:
        return 0, 0
    return await profiles_db.save_many(job.client_id, platform_id, "analysis", [_row_to_fields(r) for r in rows])
