"""The analysis catch-up safety net + the daily digest email.

Per-client `cron` discovery sweeps used to run here; that's now the always-on
round-robin engine's job (see services/round_robin_service.py), which cycles
through every client with keywords continuously instead of each one firing on
its own timer. What's left on this APScheduler instance:
  - a fixed-interval catch-up sweep: the "approve -> auto-launch analysis"
    trigger can miss (session not ready at that exact instant, process
    restarted between the approve and the job actually launching), and the
    round-robin engine's own per-client analysis step can miss the same way
   ; this periodically sweeps every client for approved-and-not-yet-analysed
    profiles and catches them up. A backstop, not the primary path.
  - the 08:00 daily digest email.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.shared.logging import get_logger

log = get_logger("scheduler")

CATCHUP_INTERVAL_MIN = 20

_scheduler = AsyncIOScheduler()
_running = False
# True while catch-up is suspended for want of any usable session. Exists
# purely so the "everything is down" incident fires once on the transition
# rather than on every 20-minute tick for as long as the outage lasts.
_breaker_open = False


async def catch_up_analysis() -> None:
    """Backstop for the approve -> auto-launch trigger missing.

    Guarded three ways against the failure this used to cause, which was
    queueing a job every 20 minutes forever into a platform that could not
    possibly run it:

    - `session_state` no longer reports a fully quarantined pool as "ready"
      (see sessions/manager.py::state_for), so a dead platform is skipped
      rather than swept.
    - If NO platform is ready, nothing is queued at all and one incident is
      raised for the whole sweep instead of one per client per tick.
    - `job_manager.create` coalesces a request into an already-queued job
      for the same client, so a backlog that outlives one catch-up interval
      cannot pile up duplicates.
    """
    from backend.database.repositories import client_repository as clients_db
    from backend.database.repositories import profile_repository as profiles_db
    from backend.services import incident_service as incidents_engine
    from backend.services import round_robin_service
    from backend.services.job_service import ANALYSIS, job_manager
    from backend.platforms import registry

    ready, unavailable = await registry.ready_platforms()
    # per-platform notification (e.g. "Facebook's session was deleted, no
    # other platform affected"), independent of the "every platform is
    # down" breaker just below, and independent of whether the round-robin
    # engine happens to be running right now. See round_robin_service's
    # module docstring for why deleting a session doesn't otherwise trigger
    # any notification at all.
    await round_robin_service.notify_unavailable_platforms(ready, unavailable)

    if not ready:
        global _breaker_open
        detail = ", ".join(f"{p}={s}" for p, s in sorted(unavailable.items())) or "no platforms enabled"
        if not _breaker_open:
            # ONE incident on the transition, not one per tick. A pool that
            # stays dead for a day used to generate 72 identical incidents
            # and 72 doomed job launches.
            _breaker_open = True
            log.error(f"catch-up suspended: no platform has a usable session ({detail})")
            await incidents_engine.record(
                "all", "analysis", "-- all clients --", "catch-up-breaker",
                "PoolExhausted",
                f"Analysis catch-up is suspended because no platform has a usable session ({detail}). "
                "Approved profiles are accumulating unanalysed and will be picked up automatically "
                "once any platform's session pool recovers.",
            )
        return
    if _breaker_open:
        _breaker_open = False
        log.info(f"catch-up resumed: {', '.join(ready)} usable again")

    for client in await clients_db.list_all():
        for platform_id in ready:
            urls = await profiles_db.urls_for(client["client_id"], platform_id, "approved", exclude_analysed=True)
            if urls:
                log.info(f"catch-up: {client['client_id']} has approved-unanalysed profiles -- launching")
                job_manager.create(ANALYSIS, client["client_id"], {}, platform=None)
                break


def sync() -> None:
    """No-op. Per-client `cron` scheduling was replaced by the always-on
    round-robin engine (see services/round_robin_service.py), which cycles
    through every client with keywords set continuously instead of firing
    each one on its own timer. Left in place (rather than removed) so
    client_controller.py's existing call sites don't need touching."""


def start() -> None:
    global _running
    if _running:
        return
    _scheduler.start()
    _running = True
    _scheduler.add_job(catch_up_analysis, trigger=IntervalTrigger(minutes=CATCHUP_INTERVAL_MIN), id="analysis_catchup", replace_existing=True)
    _scheduler.add_job(_send_daily_digest, trigger=CronTrigger.from_crontab("0 8 * * *"), id="daily_digest", replace_existing=True)
    log.info(f"APScheduler started -- analysis catch-up every {CATCHUP_INTERVAL_MIN}m, daily digest at 08:00")


async def _send_daily_digest() -> None:
    from backend.services.alerting_service import send_daily_digest
    await send_daily_digest()


def stop() -> None:
    global _running
    if _running:
        _scheduler.shutdown(wait=False)
        _running = False
        log.info("APScheduler stopped")
