"""_worker()'s outer try/except: without it, an exception _process_client()
doesn't already guard against (a Mongo hiccup in clients_db.try_get,
registry.ready_platforms(), record_run_result, ...) propagates straight out
of the coroutine. asyncio.create_task() does not restart a dead task on its
own, so that worker slot would silently stop picking up any client ever
again -- one fewer slot, forever, with nothing surfaced anywhere an
operator would see it. This is what keeps "the engine runs continuously"
actually true instead of merely assumed, and what makes sure THIS class of
failure (not just a session dying mid-scrape) still reaches an operator's
inbox via the existing debounced incident/email path.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import round_robin_service as rr


@pytest.fixture(autouse=True)
def _clean_slate():
    rr._worker_crash_incident_open.clear()
    yield
    rr._worker_crash_incident_open.clear()


class TestWorkerSurvivesUnhandledExceptions:
    @pytest.mark.asyncio
    async def test_slot_keeps_looping_past_an_unguarded_exception(self):
        calls = {"n": 0}

        async def flaky_next_client_id():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("mongo hiccup")
            if calls["n"] == 2:
                return None  # proves the loop reached a second turn alive
            raise asyncio.CancelledError()  # stop the test's own infinite loop

        with patch.object(rr, "_next_client_id", side_effect=flaky_next_client_id), \
             patch("backend.services.round_robin_service.asyncio.sleep", new_callable=AsyncMock), \
             patch("backend.services.incident_service.record", new_callable=AsyncMock) as rec:
            with pytest.raises(asyncio.CancelledError):
                await rr._worker(0)
            assert calls["n"] == 3  # crashed, recovered, crashed again (stopped)
            rec.assert_called_once()
            args = rec.call_args.args
            assert args[4] == "RoundRobinWorkerError"

    @pytest.mark.asyncio
    async def test_cancellation_is_never_swallowed(self):
        # a real cancellation (engine stop()) must propagate, not be
        # treated as just another error to recover from
        async def cancel_immediately():
            raise asyncio.CancelledError()

        with patch.object(rr, "_next_client_id", side_effect=cancel_immediately), \
             patch("backend.services.incident_service.record", new_callable=AsyncMock) as rec:
            with pytest.raises(asyncio.CancelledError):
                await rr._worker(0)
            rec.assert_not_called()


class TestCrashIncidentIsDebounced:
    @pytest.mark.asyncio
    async def test_repeated_crashes_fire_only_one_incident(self):
        calls = {"n": 0}

        async def always_crashes():
            calls["n"] += 1
            if calls["n"] >= 4:
                raise asyncio.CancelledError()
            raise RuntimeError("still broken")

        with patch.object(rr, "_next_client_id", side_effect=always_crashes), \
             patch("backend.services.round_robin_service.asyncio.sleep", new_callable=AsyncMock), \
             patch("backend.services.incident_service.record", new_callable=AsyncMock) as rec:
            with pytest.raises(asyncio.CancelledError):
                await rr._worker(0)
            rec.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_real_recovery_between_crashes_allows_a_second_incident(self):
        calls = {"n": 0}

        async def crash_then_recover_then_crash_again():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first crash")
            if calls["n"] == 2:
                return "client-a"  # goes on to a real, successful client turn
            if calls["n"] == 3:
                raise RuntimeError("second, independent crash")
            raise asyncio.CancelledError()

        with patch.object(rr, "_next_client_id", side_effect=crash_then_recover_then_crash_again), \
             patch.object(rr, "_process_client", new=AsyncMock(return_value="success")), \
             patch("backend.services.round_robin_service.asyncio.sleep", new_callable=AsyncMock), \
             patch("backend.services.incident_service.record", new_callable=AsyncMock) as rec:
            with pytest.raises(asyncio.CancelledError):
                await rr._worker(0)
            assert rec.call_count == 2
