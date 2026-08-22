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

# How late a scheduled run may start and still be worth running.
#
# APScheduler's DEFAULT is misfire_grace_time=1 SECOND (confirmed against
# the installed 3.10.4: job_defaults == {'misfire_grace_time': 1,
# 'coalesce': True, 'max_instances': 1}). A run whose trigger time passes
# while the event loop is busy for longer than that is not delayed -- it is
# DROPPED, silently, with nothing but a debug-level log line.
#
# This loop does get busy for more than a second, by design and on purpose:
# job_service.py documents blocking taskkill/join calls being moved to
# threads precisely because they used to freeze it, every running job parks
# an IPC pump, and the round-robin engine runs continuously. So the default
# meant any tick unlucky enough to land in a busy second was lost:
#   - a lost catch-up tick delays approved-but-unanalysed profiles by a
#     further 20 minutes, and nothing anywhere reports it,
#   - a lost 08:00 tick means the daily digest email is simply never sent
#     that day,
#   - a lost 03:00 tick means evidence retention does not run that night.
#
# The grace values below are sized to what each job IS, not to a single
# number: a late run of any of these is still completely useful, and none
# of them is time-critical to the second. `coalesce=True` (the default,
# stated explicitly here rather than assumed) means a backlog collapses to
# ONE run rather than firing repeatedly to catch up, and `max_instances=1`
# keeps a slow run from overlapping its own next tick.
CATCHUP_GRACE_S = 10 * 60        # half the interval; a late sweep still works
DAILY_GRACE_S = 6 * 60 * 60      # a late digest beats no digest
RETENTION_GRACE_S = 6 * 60 * 60  # a late prune beats an unbounded store

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


def register_jobs(scheduler) -> None:
    """Put this module's scheduled work onto `scheduler`.

    Split out of `start()` so the schedule itself can be asserted against
    without starting a real AsyncIOScheduler (which binds to whatever event
    loop is running at the time, making the module-level singleton
    single-use across tests). See tests_unit/test_scheduler_misfire.py.
    """
    scheduler.add_job(
        catch_up_analysis, trigger=IntervalTrigger(minutes=CATCHUP_INTERVAL_MIN),
        id="analysis_catchup", replace_existing=True,
        misfire_grace_time=CATCHUP_GRACE_S, coalesce=True, max_instances=1,
    )
    scheduler.add_job(
        _send_daily_digest, trigger=CronTrigger.from_crontab("0 8 * * *"),
        id="daily_digest", replace_existing=True,
        misfire_grace_time=DAILY_GRACE_S, coalesce=True, max_instances=1,
    )
    scheduler.add_job(
        prune_expired_evidence, trigger=CronTrigger.from_crontab("0 3 * * *"),
        id="evidence_retention", replace_existing=True,
        misfire_grace_time=RETENTION_GRACE_S, coalesce=True, max_instances=1,
    )


def start() -> None:
    global _running
    if _running:
        return
    _scheduler.start()
    _running = True
    register_jobs(_scheduler)
    log.info(f"APScheduler started -- analysis catch-up every {CATCHUP_INTERVAL_MIN}m, daily digest at 08:00, evidence retention daily at 03:00")
    import asyncio
    try:
        asyncio.create_task(prune_expired_evidence())
    except RuntimeError:
        pass


async def prune_expired_evidence() -> None:
    """Automatically delete evidence screenshots and chunks older than retention policy."""
    from backend.config.settings import settings
    from backend.database.repositories import evidence_repository
    days = settings.evidence_retention_days
    if days > 0:
        await evidence_repository.delete_older_than(days)
        await evidence_repository.cleanup_orphaned_chunks()


async def _send_daily_digest() -> None:
    from backend.services.alerting_service import send_daily_digest
    await send_daily_digest()


def stop() -> None:
    global _running
    if _running:
        _scheduler.shutdown(wait=False)
        _running = False
        log.info("APScheduler stopped")

