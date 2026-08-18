"""An operator hitting Stop on a run the round-robin engine started must
abort that run only -- not the slot that was waiting on it.

`_process_client` used to `await job.task` directly. Awaiting a task that
someone else cancelled re-raises CancelledError in the *awaiter*, and both
`_process_client` and `_worker` re-raise CancelledError on purpose (that is
how `stop()` shuts a slot down). So one Stop click silently and permanently
killed a round-robin slot, while `status()` kept counting it as alive --
the engine just quietly ran with fewer workers until the next restart.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import round_robin_service as rr
from backend.services.job_service import CANCELLED, DONE


class _FakeJob:
    """Just the surface `_await_job` touches: an id, a task, a status."""

    def __init__(self, job_id: str):
        self.id = job_id
        self.status = "running"
        self.task = asyncio.create_task(asyncio.sleep(30))

    def user_cancels(self) -> None:
        """What POST /jobs/{id}/cancel does: cancel the supervising task.
        _guard sets CANCELLED on its way out; do that here directly."""
        self.status = CANCELLED
        self.task.cancel()


class TestAwaitJobAbsorbsTheJobsOwnCancellation:
    @pytest.mark.asyncio
    async def test_a_cancelled_job_does_not_cancel_its_waiter(self):
        job = _FakeJob("j1")
        waiter = asyncio.create_task(rr._await_job(job))
        await asyncio.sleep(0)
        job.user_cancels()

        assert await waiter == CANCELLED
        assert not waiter.cancelled()

    @pytest.mark.asyncio
    async def test_engine_stop_still_cancels_the_waiter(self):
        # the distinction that matters: cancelling the WORKER (engine stop)
        # must still propagate, only the JOB's own cancellation is absorbed
        job = _FakeJob("j2")
        waiter = asyncio.create_task(rr._await_job(job))
        await asyncio.sleep(0)
        waiter.cancel()

        with pytest.raises(asyncio.CancelledError):
            await waiter
        job.task.cancel()

    @pytest.mark.asyncio
    async def test_a_finished_job_reports_its_status(self):
        job = _FakeJob("j3")
        job.task.cancel()  # stand-in for "the task ended"
        job.status = DONE
        assert await rr._await_job(job) == DONE


class TestOperatorAbortIsNotAFailure:
    @pytest.mark.asyncio
    async def test_turn_is_recorded_skipped_not_failed(self):
        """A deliberate Stop must not feed the consecutive-failure counter:
        five of them in a row would otherwise trip the "job worker spawn is
        degraded" incident and put every slot into a 2-minute backoff."""
        created: list = []

        def fake_create(kind, client_id, params, **kwargs):
            job = _FakeJob(f"job-{len(created)}")
            job.status = CANCELLED
            job.task.cancel()
            created.append(job)
            return job

        recorded: dict = {}

        async def fake_record(client_id, status, note, duration_s=0.0):
            recorded.update(client_id=client_id, status=status, note=note)

        with patch("backend.database.repositories.client_repository.try_get",
                   new=AsyncMock(return_value={"client_id": "c1", "name_keywords": ["acme"]})), \
             patch("backend.database.repositories.client_repository.record_run_result",
                   new=AsyncMock(side_effect=fake_record)), \
             patch("backend.platforms.registry.ready_platforms",
                   new=AsyncMock(return_value=(["facebook"], {}))), \
             patch.object(rr, "notify_unavailable_platforms", new=AsyncMock()), \
             patch("backend.services.job_service.job_manager.create", side_effect=fake_create):
            result = await rr._process_client(0, "c1")

        assert result == "skipped"
        assert recorded["status"] == "skipped"
        assert "operator" in recorded["note"]
        # discovery only: an aborted sweep must not fall through into the
        # analysis phase for the same client
        assert len(created) == 1

    @pytest.mark.asyncio
    async def test_skipped_does_not_climb_the_failure_streak(self):
        calls = {"n": 0}

        async def two_turns_then_stop():
            calls["n"] += 1
            if calls["n"] > 2:
                raise asyncio.CancelledError()
            return "c1"

        rr._consecutive_failures = 0
        with patch.object(rr, "_next_client_id", side_effect=two_turns_then_stop), \
             patch.object(rr, "_process_client", new=AsyncMock(return_value="skipped")), \
             patch("backend.services.round_robin_service.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(asyncio.CancelledError):
                await rr._worker(0)

        assert rr._consecutive_failures == 0


class TestEngineStopAbortsInFlightJobs:
    def test_stop_cancels_jobs_the_engine_owns(self):
        """Cancelling a worker task only unblocks the worker; the job it was
        waiting on is a separate task with its own child process and would
        otherwise keep scraping behind a Scheduler tab reading 'stopped'."""
        rr._running = True
        rr._tasks.clear()
        rr._engine_job_ids.clear()
        rr._engine_job_ids.update({"a", "b"})

        cancelled: list[str] = []
        with patch("backend.services.job_service.job_manager.cancel",
                   side_effect=lambda jid: cancelled.append(jid) or True):
            rr.stop()

        assert sorted(cancelled) == ["a", "b"]
        assert not rr._engine_job_ids
