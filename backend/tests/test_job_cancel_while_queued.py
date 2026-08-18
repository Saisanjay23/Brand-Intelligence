"""Cancelling a job that has not started yet must still mark it CANCELLED.

A QUEUED job is parked on `_hold_locks`, waiting for whatever holds its
platform locks to finish. `_run`'s own `except asyncio.CancelledError`
only covers the stretch AFTER the worker process is spawned, so a cancel
delivered while the job was still queued unwound straight out of `_guard`
with nothing marking the job terminal: it sat at "queued" forever.

That is not an edge case -- it is what happens every time the round-robin
scheduler is mid-sweep. The operator's manual Discover/Analyse queues
behind it, and Stop leaves a job the UI must keep reporting as active
(`queued` is not terminal) with no way left to clear it.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.services.job_service import (
    CANCELLED,
    DISCOVERY,
    QUEUED,
    RUNNING,
    JobManager,
)


async def _settle():
    """Let the cancellation unwind through the guard."""
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.fixture
def manager(monkeypatch):
    # JobManager is a process-wide singleton, so `locks` (and the futures
    # inside them, bound to the previous test's now-closed event loop)
    # would otherwise leak from one test into the next.
    mgr = JobManager()
    mgr._init()

    # Stands in for the real _run: takes the same locks (that is what makes
    # the second job QUEUE) but never spawns a worker process.
    async def never_finishes(self, job):
        async with self._hold_locks(job):
            job.status = RUNNING
            await asyncio.Event().wait()

    monkeypatch.setattr(JobManager, "_run", never_finishes)
    return mgr


class TestCancelBeforeStart:
    @pytest.mark.asyncio
    async def test_a_queued_job_reaches_cancelled(self, manager):
        holder = manager.create(DISCOVERY, "c1", {}, platform="facebook")
        await _settle()
        assert holder.status == RUNNING

        queued = manager.create(DISCOVERY, "c1", {}, platform="facebook")
        await _settle()
        # it never started: the lock is held by `holder`
        assert queued.status == QUEUED

        assert manager.cancel(queued.id) is True
        await _settle()

        assert queued.status == CANCELLED, "stuck queued -- Stop can never clear it"
        assert queued.finished
        assert any(e.type == CANCELLED for e in queued.events)

        holder.task.cancel()
        await _settle()

    @pytest.mark.asyncio
    async def test_cancelling_the_queue_head_lets_the_next_job_run(self, manager):
        holder = manager.create(DISCOVERY, "c1", {}, platform="facebook")
        await _settle()
        first = manager.create(DISCOVERY, "c2", {}, platform="facebook")
        await _settle()
        assert first.status == QUEUED

        manager.cancel(first.id)
        holder.task.cancel()
        await _settle()

        assert first.status == CANCELLED
        # the lock it was queued on is released, not leaked
        assert all(not lock.locked() for lock in manager.locks.values())

    @pytest.mark.asyncio
    async def test_cancel_is_idempotent_once_terminal(self, manager):
        job = manager.create(DISCOVERY, "c1", {}, platform="facebook")
        await _settle()
        assert manager.cancel(job.id) is True
        await _settle()
        assert job.status == CANCELLED
        # a second click must report "nothing to cancel", not a fresh success
        assert manager.cancel(job.id) is False

    @pytest.mark.asyncio
    async def test_cancel_of_an_unknown_job_is_false(self, manager):
        assert manager.cancel("nope") is False
