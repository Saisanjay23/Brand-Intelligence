"""Scheduled runs must survive a busy event loop.

APScheduler's default `misfire_grace_time` is ONE SECOND. A trigger time
that passes while the loop is blocked for longer than that does not run
late -- it is dropped, silently, with only a debug log line to say so.

This loop blocks for more than a second as a matter of course (see
job_service.py, which moves taskkill/join off it for exactly that reason,
and round_robin_service.py, which runs continuously). So the default meant
the daily digest could simply not be sent on a day when 08:00 happened to
land in a busy second, with nothing anywhere reporting it.

These assertions are on the REGISTERED jobs, not on the constants, so
deleting the arguments at the call site fails the test.
"""

from __future__ import annotations

import pytest

from backend.services import scheduler_service as sched


@pytest.fixture
def started():
    """A throwaway scheduler carrying the real schedule.

    Deliberately never started: AsyncIOScheduler.start() binds to whatever
    event loop is running, which makes the module-level singleton usable
    exactly once across a test session. `register_jobs` is the real
    registration code either way, so this asserts the shipping schedule.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler()
    sched.register_jobs(scheduler)
    return scheduler


EXPECTED = {
    "analysis_catchup": sched.CATCHUP_GRACE_S,
    "daily_digest": sched.DAILY_GRACE_S,
    "evidence_retention": sched.RETENTION_GRACE_S,
}


class TestMisfireGrace:
    def test_every_scheduled_job_is_registered(self, started):
        ids = {j.id for j in started.get_jobs()}
        assert EXPECTED.keys() <= ids

    @pytest.mark.parametrize("job_id", sorted(EXPECTED))
    def test_grace_is_generous_not_the_one_second_default(self, started, job_id):
        job = started.get_job(job_id)
        assert job is not None
        assert job.misfire_grace_time == EXPECTED[job_id]
        # the actual regression guard: anything near the library default
        # means a busy second silently skips the run
        assert job.misfire_grace_time >= 60

    @pytest.mark.parametrize("job_id", sorted(EXPECTED))
    def test_a_backlog_collapses_to_one_run(self, started, job_id):
        """With a long grace, coalesce is what stops a delayed job firing
        once per missed tick when the loop frees up."""
        assert started.get_job(job_id).coalesce is True

    @pytest.mark.parametrize("job_id", sorted(EXPECTED))
    def test_a_slow_run_never_overlaps_itself(self, started, job_id):
        assert started.get_job(job_id).max_instances == 1


class TestCatchupGraceFitsItsInterval:
    def test_grace_is_under_a_full_interval(self):
        """Longer than the interval would let a run start so late it
        races the next tick; coalesce would merge them, but the run would
        no longer mean what its schedule says."""
        assert sched.CATCHUP_GRACE_S < sched.CATCHUP_INTERVAL_MIN * 60
