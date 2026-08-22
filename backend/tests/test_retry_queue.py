"""The retry queue: what an analyst sees for a profile analysis has not
finished with, and the Stop/Resume actions that control it.

Live-verified against the real database (2026-08-22) before these were
written: a real Twitter row moved eligible -> stopped -> eligible through
these exact endpoints, `urls_for(exclude_analysed=True)` genuinely dropped
its URL the instant it was stopped and picked it back up the instant it was
resumed, and `coverage()`'s `blocked` list reported "manually stopped" as
the reason instead of the row's (perfectly fine) analysis_status. These
tests pin that behaviour with mocks, following this suite's existing
convention (see test_force_reanalysis.py, test_evidence.py) of not opening
a live Mongo connection from pytest.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services import profile_service as svc


def _doc(**over) -> dict:
    base = {
        "id": "p1", "client_id": "c1", "platform": "twitter",
        "url": "https://x.com/someone", "status": "approved", "phase": "analysis",
        "display_name": "Someone", "analysis_status": "OK",
        "analysis_attempts": 0, "analysis_complete": True,
        "field_status": {}, "retry_disabled": False, "has_logo": False,
    }
    base.update(over)
    return base


# 1. classification -- pure functions, no mocks

class TestRetryState:
    def test_a_fresh_ok_row_reads_eligible(self):
        assert svc._retry_state(_doc()) == "eligible"

    def test_under_the_attempt_cap_is_eligible(self):
        doc = _doc(analysis_complete=False, analysis_attempts=svc.profiles_db.MAX_ANALYSIS_ATTEMPTS - 1)
        assert svc._retry_state(doc) == "eligible"

    def test_at_the_attempt_cap_is_exhausted(self):
        doc = _doc(analysis_complete=False, analysis_attempts=svc.profiles_db.MAX_ANALYSIS_ATTEMPTS)
        assert svc._retry_state(doc) == "exhausted"

    def test_retry_disabled_wins_even_over_exhausted(self):
        # An analyst who stopped an already-exhausted row should see
        # "stopped" (something they can Resume), not "exhausted" (which
        # reads as permanent and unrelated to anything they did).
        doc = _doc(analysis_complete=False,
                   analysis_attempts=svc.profiles_db.MAX_ANALYSIS_ATTEMPTS + 3,
                   retry_disabled=True)
        assert svc._retry_state(doc) == "stopped"


class TestRetryReason:
    def test_a_never_reached_status_names_itself(self):
        doc = _doc(analysis_status="CHECKPOINT")
        assert svc._retry_reason(doc) == "never reached (CHECKPOINT)"

    def test_a_reached_but_incomplete_row_lists_the_missed_fields(self):
        doc = _doc(analysis_status="OK", field_status={
            "display_name": "read", "location": "none-exist",
            "last_post_date": "MISSED", "screenshot": "MISSED",
        })
        reason = svc._retry_reason(doc)
        assert "last_post_date" in reason
        assert "screenshot" in reason
        assert "location" not in reason  # none-exist is not a miss

    def test_read_and_not_collected_never_appear_as_reasons(self):
        doc = _doc(field_status={"display_name": "read", "location": "not-collected"})
        assert "display_name" not in svc._retry_reason(doc)
        assert "location" not in svc._retry_reason(doc)


# 2. retry_queue / stop / resume -- service layer, repository mocked

@pytest.mark.asyncio
async def test_retry_queue_counts_each_state_once():
    docs = [
        _doc(id="a", analysis_status="ERROR"),                                    # eligible
        _doc(id="b", analysis_complete=False, analysis_attempts=99),              # exhausted
        _doc(id="c", retry_disabled=True),                                        # stopped
    ]
    with patch("backend.services.profile_service.profiles_db.retry_queue_profiles",
               new=AsyncMock(return_value=docs)), \
         patch("backend.services.profile_service.clients_db.try_get", new=AsyncMock(return_value=None)):
        out = await svc.retry_queue("c1")
    assert out["total"] == 3
    assert out["counts"] == {"eligible": 1, "exhausted": 1, "stopped": 1}


@pytest.mark.asyncio
async def test_stop_retry_calls_the_repository_with_disabled_true():
    with patch("backend.services.profile_service.profiles_db.set_retry_state",
               new=AsyncMock(return_value=_doc(retry_disabled=True))) as set_state, \
         patch("backend.services.profile_service.clients_db.try_get", new=AsyncMock(return_value=None)):
        out = await svc.stop_retry("p1")
    set_state.assert_awaited_once_with("p1", disabled=True)
    assert out["retry_state"] == "stopped"


@pytest.mark.asyncio
async def test_resume_retry_clears_the_flag_and_resets_attempts():
    # The regression this guards: clearing retry_disabled WITHOUT
    # resetting analysis_attempts would leave urls_for's attempts<MAX
    # condition still failing for a row that had hit the cap, so Resume
    # would silently do nothing for exactly the rows it exists to fix.
    with patch("backend.services.profile_service.profiles_db.set_retry_state",
               new=AsyncMock(return_value=_doc(analysis_attempts=0))) as set_state, \
         patch("backend.services.profile_service.clients_db.try_get", new=AsyncMock(return_value=None)):
        await svc.resume_retry("p1")
    set_state.assert_awaited_once_with("p1", disabled=False, reset_attempts=True)


@pytest.mark.asyncio
async def test_stop_retry_on_a_missing_profile_raises_not_found():
    from backend.shared.errors import NotFoundError
    with patch("backend.services.profile_service.profiles_db.set_retry_state",
               new=AsyncMock(return_value=None)):
        with pytest.raises(NotFoundError):
            await svc.stop_retry("does-not-exist")


@pytest.mark.asyncio
async def test_bulk_stop_retry_reports_a_bad_id_instead_of_raising():
    async def _set_state(pid, *, disabled, reset_attempts=False):
        if pid == "bad":
            raise Exception("not found")
        return _doc(id=pid)

    with patch("backend.services.profile_service.profiles_db.set_retry_state", new=_set_state):
        out = await svc.bulk_stop_retry(["good", "bad"])
    assert out == {"succeeded": ["good"], "failed": ["bad"]}


# 3. coverage() surfaces the manually-stopped reason

@pytest.mark.asyncio
async def test_coverage_prefers_stuck_analysis_own_reason():
    # The bug caught live: coverage() used to read analysis_status directly
    # and ignore the "manually stopped" reason stuck_analysis had already
    # computed, so a perfectly healthy OK row an analyst had stopped showed
    # up in the coverage report as blocked-because-"OK", which reads as
    # nonsensical rather than as an analyst's own decision.
    blocked = [{
        "id": "p1", "url": "https://x.com/a", "platform": "twitter",
        "display_name": "A", "reason": "manually stopped",
        "analysis_status": "OK", "analysis_attempts": 0, "comments": "",
    }]
    with patch("backend.services.profile_service.profiles_db.stats",
               new=AsyncMock(return_value={})), \
         patch("backend.services.profile_service.profiles_db.stuck_analysis",
               new=AsyncMock(return_value=blocked)):
        out = await svc.coverage("c1")
    assert out["blocked"][0]["reason"] == "manually stopped"


@pytest.mark.asyncio
async def test_coverage_still_falls_back_to_analysis_status_normally():
    blocked = [{
        "id": "p1", "url": "https://x.com/a", "platform": "twitter",
        "display_name": "A", "analysis_status": "CHECKPOINT",
        "analysis_attempts": 4, "comments": "",
    }]
    with patch("backend.services.profile_service.profiles_db.stats",
               new=AsyncMock(return_value={})), \
         patch("backend.services.profile_service.profiles_db.stuck_analysis",
               new=AsyncMock(return_value=blocked)):
        out = await svc.coverage("c1")
    assert out["blocked"][0]["reason"] == "CHECKPOINT"
