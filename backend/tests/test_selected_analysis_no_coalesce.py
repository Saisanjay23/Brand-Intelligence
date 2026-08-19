"""A profile_ids analysis job (re-analyse THESE specific profiles, the
Analysis view's "Re-analyse Selected" action) must never be coalesced
with, or absorb, a plain analysis job for the same client.

`job_manager` coalesces same-client analysis jobs on the theory that a
platform=None job "sweeps everything approved" and therefore subsumes any
narrower request -- true for the plain/force jobs that theory was written
for, but a profile_ids job also reports platform=None (its selection can
span platforms) while covering only its own hand-picked ids. Without an
explicit exemption in BOTH directions:

  * a profile_ids request arriving while a plain job is already queued
    would get folded into that job and silently lose its selection
    (re-analysing whatever is generically owed instead of the profiles
    the analyst actually picked);
  * a later plain "approve -> auto-analyse" request for a different
    profile would get folded into an already-queued profile_ids job,
    which never analyses it, leaving that profile approved and
    permanently un-analysed with nothing to notice.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.services.job_service import ANALYSIS, JobManager


@pytest.fixture
def manager(monkeypatch):
    mgr = JobManager()
    mgr._init()

    async def never_finishes(self, job):
        async with self._hold_locks(job):
            job.status = "running"
            await asyncio.Event().wait()

    monkeypatch.setattr(JobManager, "_run", never_finishes)
    return mgr


async def _settle():
    for _ in range(20):
        await asyncio.sleep(0)


class TestProfileIdsJobIsNeverCoalescedInto:
    @pytest.mark.asyncio
    async def test_a_second_profile_ids_request_gets_its_own_job(self, manager):
        first = manager.create(ANALYSIS, "c1", {"profile_ids": ["a", "b"]}, platform=None)
        await _settle()
        second = manager.create(ANALYSIS, "c1", {"profile_ids": ["c"]}, platform=None)
        assert second.id != first.id
        assert second.params["profile_ids"] == ["c"]
        first.task.cancel()
        second.task.cancel()

    @pytest.mark.asyncio
    async def test_a_plain_request_does_not_absorb_into_a_queued_profile_ids_job(self, manager):
        """The reverse direction: an unrelated auto-triggered analysis must
        not disappear into a narrow selected-profiles job."""
        selected = manager.create(ANALYSIS, "c1", {"profile_ids": ["a"]}, platform=None)
        await _settle()
        auto = manager.create(ANALYSIS, "c1", {}, platform=None)
        assert auto.id != selected.id
        selected.task.cancel()
        auto.task.cancel()


class TestProfileIdsJobDoesNotStealFromAPlainJob:
    @pytest.mark.asyncio
    async def test_a_profile_ids_request_never_reuses_a_queued_plain_job(self, manager):
        plain = manager.create(ANALYSIS, "c1", {}, platform=None)
        await _settle()
        selected = manager.create(ANALYSIS, "c1", {"profile_ids": ["a", "b"]}, platform=None)
        assert selected.id != plain.id
        assert selected.params["profile_ids"] == ["a", "b"]
        plain.task.cancel()
        selected.task.cancel()


class TestOrdinaryCoalescingStillWorks:
    """The fix must be scoped to profile_ids specifically -- the existing
    behaviour (200 Validate clicks collapsing into one queued job) is not
    something this should touch."""

    @pytest.mark.asyncio
    async def test_two_plain_requests_still_coalesce(self, manager):
        # No await between the two creates: existing_queued() only matches
        # QUEUED (never RUNNING) jobs, and a job's stub _run only flips it
        # to running once its own task actually gets a turn on the loop --
        # which asyncio.create_task schedules but does not run
        # synchronously. Yielding here (as every other test in this file
        # does, to let jobs settle into "running" before asserting on
        # them) would defeat the exact scenario this test needs: two
        # requests arriving close enough together that the first is still
        # QUEUED when the second is made.
        first = manager.create(ANALYSIS, "c1", {}, platform=None)
        second = manager.create(ANALYSIS, "c1", {}, platform=None)
        assert second.id == first.id
        first.task.cancel()

    @pytest.mark.asyncio
    async def test_force_still_never_coalesces(self, manager):
        plain = manager.create(ANALYSIS, "c1", {}, platform=None)
        forced = manager.create(ANALYSIS, "c1", {"force": True}, platform=None)
        assert forced.id != plain.id
        plain.task.cancel()
        forced.task.cancel()
