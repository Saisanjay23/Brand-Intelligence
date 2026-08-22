"""The background session monitor must keep recording a session's failures
for as long as it stays broken, not only the first time.

THE BUG THIS GUARDS
    `_record_item_result` used to gate its call to `mark_session_failed` on
    whether the PREVIOUSLY recorded health check had passed:

        was_ok = previous is None or previous.get("ok", True)
        if was_ok and not ok:
            await mark_session_failed(...)

    So only the very first failing check in a session's life ever reached
    `mark_session_failed`. Every later 30-minute recheck of an
    already-broken session hit the `was_ok=False` branch and did nothing at
    all -- `consecutive_failures` froze at 1 forever, the backoff ladder
    never advanced past its first tier, and the Sessions panel showed "1st
    consecutive failure" on an account that had in fact failed every check
    for weeks.

    The gate was based on a misunderstanding: `mark_session_failed` already
    de-duplicates the SessionInvalid incident itself, via its own
    `newly_dead` check against the session's CURRENT STORED STATUS (not
    against this function's health-check history), so gating the call a
    second time here was both redundant and wrong. Every live-job failure
    path (analysis_service.py, discovery_service.py) already calls
    `mark_session_failed` unconditionally on every failure; this brings the
    background monitor in line with that.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.sessions import manager as sessions_mgr


@pytest.mark.asyncio
async def test_a_second_consecutive_failure_still_calls_mark_session_failed():
    with patch.object(sessions_mgr.sessions_db, "record_item_health",
                      new=AsyncMock(return_value={"ok": False})), \
         patch.object(sessions_mgr, "mark_session_failed", new=AsyncMock()) as mark_failed:
        await sessions_mgr._record_item_result(
            "twitter", "s1", "acct", ok=False, detail="checkpoint", conclusive=True)
    mark_failed.assert_awaited_once_with("twitter", "s1", "expired", detail="checkpoint")


@pytest.mark.asyncio
async def test_the_very_first_failure_also_calls_it():
    """The regression the old code got right by accident -- confirming the
    fix did not flip this into ALSO skipping the first failure."""
    with patch.object(sessions_mgr.sessions_db, "record_item_health",
                      new=AsyncMock(return_value=None)), \
         patch.object(sessions_mgr, "mark_session_failed", new=AsyncMock()) as mark_failed:
        await sessions_mgr._record_item_result(
            "twitter", "s1", "acct", ok=False, detail="checkpoint", conclusive=True)
    mark_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_passing_check_clears_quarantine_instead():
    with patch.object(sessions_mgr.sessions_db, "record_item_health",
                      new=AsyncMock(return_value={"ok": False})), \
         patch.object(sessions_mgr, "mark_session_ok", new=AsyncMock()) as mark_ok, \
         patch.object(sessions_mgr, "mark_session_failed", new=AsyncMock()) as mark_failed:
        await sessions_mgr._record_item_result(
            "twitter", "s1", "acct", ok=True, detail="", conclusive=True)
    mark_ok.assert_awaited_once_with("twitter", "s1")
    mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_inconclusive_check_touches_neither():
    """A network blip must never be recorded as evidence the SESSION is
    the problem -- see verify_session_item's own docstring on this
    distinction."""
    with patch.object(sessions_mgr.sessions_db, "record_item_health",
                      new=AsyncMock(return_value=None)), \
         patch.object(sessions_mgr, "mark_session_ok", new=AsyncMock()) as mark_ok, \
         patch.object(sessions_mgr, "mark_session_failed", new=AsyncMock()) as mark_failed:
        await sessions_mgr._record_item_result(
            "twitter", "s1", "acct", ok=False, detail="timeout", conclusive=False)
    mark_ok.assert_not_awaited()
    mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_consecutive_failures_actually_climbs_across_real_calls():
    """End-to-end through the real mark_session_failed (only the DB layer
    mocked), the property that actually matters: a session failing its
    health check three sweeps in a row shows failure #3, not #1 forever."""
    stored: dict = {"status": "ready", "consecutive_failures": 0}

    async def fake_get_item(_platform, _id):
        return dict(stored)

    async def fake_update_item(_platform, _id, **fields):
        stored.update(fields)
        return True

    with patch.object(sessions_mgr.sessions_db, "record_item_health",
                      new=AsyncMock(return_value={"ok": False})), \
         patch.object(sessions_mgr.sessions_db, "get_item", new=fake_get_item), \
         patch.object(sessions_mgr.sessions_db, "update_item", new=fake_update_item), \
         patch("backend.services.incident_service.record", new=AsyncMock()):
        for _ in range(3):
            await sessions_mgr._record_item_result(
                "twitter", "s1", "acct", ok=False, detail="checkpoint", conclusive=True)

    assert stored["consecutive_failures"] == 3
