"""Per-client cron discovery sweeps + the analysis catch-up safety net.

Two jobs run on this APScheduler instance:
  - one cron trigger per client that has both `cron` and `keywords` set,
    synced whenever a client is created/updated (`sync()`)
  - a fixed-interval catch-up sweep: the "approve -> auto-launch analysis"
    trigger can miss (session not ready at that exact instant, process
    restarted between the approve and the job actually launching) -- this
    periodically sweeps every client for approved-and-not-yet-analysed
    profiles and catches them up. A backstop, not the primary path.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger("bi.scheduler")

CATCHUP_INTERVAL_MIN = 20

_scheduler = AsyncIOScheduler()
_running = False


async def catch_up_analysis() -> None:
    from backend.db import clients as clients_db
    from backend.db import profiles as profiles_db
    from backend.engine.jobs import ANALYSIS, job_manager
    from backend.platforms import registry

    clients = await clients_db.list_all()
    for client in clients:
        has_backlog = False
        for platform_id, plat in registry.PLATFORMS.items():
            if not plat.enabled or await registry.session_state(plat) != "ready":
                continue
            urls = await profiles_db.urls_for(client["client_id"], platform_id, "approved", exclude_analysed=True)
            if urls:
                has_backlog = True
                break
        if has_backlog:
            log.info(f"catch-up: {client['client_id']} has approved-unanalysed profiles -- launching")
            job_manager.create(ANALYSIS, client["client_id"], {}, platform=None)


async def trigger_discovery(client_id: str, keywords: list[str]) -> None:
    """Fired by the cron scheduler to sweep every ready platform for a client."""
    from backend.engine.jobs import DISCOVERY, job_manager

    log.info(f"cron trigger for {client_id}: discovery starting with {len(keywords)} keyword(s)")
    job_manager.create(DISCOVERY, client_id, {
        "keywords": keywords, "tabs": ["people", "pages"], "max_results": 0, "max_seconds": 1800,
    })


async def _sync_jobs() -> None:
    from backend.db import clients as clients_db
    from backend.engine.alerting import send_daily_digest

    clients = await clients_db.list_all()

    for job in _scheduler.get_jobs():
        if job.id.startswith("client_cron_") or job.id == "daily_digest":
            _scheduler.remove_job(job.id)

    _scheduler.add_job(send_daily_digest, trigger=CronTrigger.from_crontab("0 8 * * *"), id="daily_digest", replace_existing=True)

    added = 0
    for c in clients:
        cron = c.get("cron")
        keywords = c.get("keywords") or []
        if not cron or not keywords:
            continue
        try:
            trigger = CronTrigger.from_crontab(cron)
            _scheduler.add_job(trigger_discovery, trigger=trigger, args=[c["client_id"], keywords],
                                id=f"client_cron_{c['client_id']}", replace_existing=True)
            added += 1
        except Exception as e:
            log.error(f"invalid cron {cron!r} for {c['client_id']}: {e}")

    log.info(f"scheduler synced: {added} active client schedule(s)")


def sync() -> None:
    """Triggered by the clients API whenever a client is added/updated."""
    if _running:
        asyncio.create_task(_sync_jobs())


def start() -> None:
    global _running
    if _running:
        return
    _scheduler.start()
    _running = True
    _scheduler.add_job(catch_up_analysis, trigger=IntervalTrigger(minutes=CATCHUP_INTERVAL_MIN), id="analysis_catchup", replace_existing=True)
    asyncio.create_task(_sync_jobs())
    log.info(f"APScheduler started -- analysis catch-up every {CATCHUP_INTERVAL_MIN}m")


def stop() -> None:
    global _running
    if _running:
        _scheduler.shutdown(wait=False)
        _running = False
        log.info("APScheduler stopped")
