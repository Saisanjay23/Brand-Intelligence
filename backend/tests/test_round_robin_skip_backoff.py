"""_worker() must pause when _process_client returns "skipped" (no platform
had a usable session), exactly like it already does for "failed" -- without
it, the loop re-checks the next client immediately, and with every platform
down that means every client in the rotation skips in a tight loop with no
delay at all, burning a full CPU core per slot and hammering Mongo with
record_run_result writes for a client turn nothing will succeed on until
some session recovers.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import round_robin_service as rr


@pytest.mark.asyncio
async def test_a_skipped_turn_sleeps_before_the_next_client():
    calls = {"n": 0}

    async def flaky_next_client_id():
        calls["n"] += 1
        if calls["n"] >= 2:
            raise asyncio.CancelledError()  # stop the loop after one real turn
        return "client-a"

    with patch.object(rr, "_next_client_id", side_effect=flaky_next_client_id), \
         patch.object(rr, "_process_client", new=AsyncMock(return_value="skipped")), \
         patch("backend.services.round_robin_service.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        with pytest.raises(asyncio.CancelledError):
            await rr._worker(0)

        sleep_mock.assert_awaited_once_with(5)


@pytest.mark.asyncio
async def test_consecutive_failures_counter_is_unaffected_by_a_skip():
    """A skip is not a failure -- it must not feed the escalating backoff
    _consecutive_failures drives, or a genuinely-down session pool would
    make the engine back off further and further for no reason tied to any
    actual error."""
    rr._consecutive_failures = 3
    try:
        calls = {"n": 0}

        async def flaky_next_client_id():
            calls["n"] += 1
            if calls["n"] >= 2:
                raise asyncio.CancelledError()
            return "client-a"

        with patch.object(rr, "_next_client_id", side_effect=flaky_next_client_id), \
             patch.object(rr, "_process_client", new=AsyncMock(return_value="skipped")), \
             patch("backend.services.round_robin_service.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(asyncio.CancelledError):
                await rr._worker(0)

        assert rr._consecutive_failures == 3  # untouched
    finally:
        rr._consecutive_failures = 0
