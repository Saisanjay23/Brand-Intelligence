"""_check_last_post_extraction_health(): the analysis-phase equivalent of
discovery's parser-drift canary. A platform changing its page/payload shape
doesn't raise an exception during analysis -- every last-post tier just
comes up empty, silently, on an otherwise-successful (status OK) row. This
is what turns that into an incident (and therefore an email, via the
existing debounced alerting_service path) instead of a quietly-degrading
report.
"""

from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.analysis_service import _check_last_post_extraction_health
from backend.services.job_service import Job
from backend.shared.models.row import Row


def _job() -> Job:
    return Job(id="job1", kind="analysis", client_id="client1", platform="facebook", params={})


_seq = itertools.count()


def _row(status: str = "OK", posts_seen: str = "", url: str = "") -> Row:
    """Each call is a DISTINCT profile unless a url is passed explicitly.

    These fixtures used to share one default url, which made a list of six
    rows look like six profiles to the canary when it is really one profile
    attempted six times -- the exact confusion `_canary_sample` now removes
    (see tests/test_canary_false_positives.py). Distinct urls are also what
    a real batch looks like, so the sensitivity these tests assert is the
    sensitivity production actually gets.
    """
    r = Row(url=url or f"https://x.com/p{next(_seq)}", target="Adani")
    r.status = status
    r.posts_seen = posts_seen
    return r


class TestFiresOnMajorityBlank:
    @pytest.mark.asyncio
    async def test_majority_blank_ok_rows_raises_an_incident(self):
        # 5 OK rows, 3 blank (neither "no" nor "yes") -- 60% >= the 50% bar
        rows = [_row(posts_seen="") for _ in range(3)] + [_row(posts_seen="yes") for _ in range(2)]
        with patch("backend.services.analysis_service.incidents_engine.record", new_callable=AsyncMock) as rec:
            _check_last_post_extraction_health("facebook", _job(), rows)
            await _drain()
            rec.assert_called_once()
            args, kwargs = rec.call_args
            assert args[0] == "facebook"
            assert args[1] == "analysis"
            assert args[4] == "LastPostExtractionDrift"
            assert "read_last_post" in kwargs["where"]


class TestDoesNotFireOnHealthyBatches:
    @pytest.mark.asyncio
    async def test_all_rows_have_a_real_date_or_confirmed_no_posts(self):
        rows = [_row(posts_seen="yes") for _ in range(4)] + [_row(posts_seen="no")]
        with patch("backend.services.analysis_service.incidents_engine.record", new_callable=AsyncMock) as rec:
            _check_last_post_extraction_health("facebook", _job(), rows)
            await _drain()
            rec.assert_not_called()

    @pytest.mark.asyncio
    async def test_too_few_ok_rows_to_mean_anything(self):
        # only 3 OK rows total (below the 5-row floor), even if all blank
        rows = [_row(posts_seen="") for _ in range(3)]
        with patch("backend.services.analysis_service.incidents_engine.record", new_callable=AsyncMock) as rec:
            _check_last_post_extraction_health("facebook", _job(), rows)
            await _drain()
            rec.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_minority_blank_is_not_drift_just_a_few_hard_profiles(self):
        # 5 OK rows, only 2 blank (40%, below the 50% bar and the count>=3 floor)
        rows = [_row(posts_seen="") for _ in range(2)] + [_row(posts_seen="yes") for _ in range(3)]
        with patch("backend.services.analysis_service.incidents_engine.record", new_callable=AsyncMock) as rec:
            _check_last_post_extraction_health("facebook", _job(), rows)
            await _drain()
            rec.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_and_partial_rows_are_excluded_from_the_denominator(self):
        # a session dying mid-batch produces ERROR rows -- that is a session
        # problem, already reported elsewhere, and must not count toward
        # (or dilute) the extraction-drift signal
        rows = (
            [_row(status="ERROR", posts_seen="") for _ in range(10)]
            + [_row(status="OK", posts_seen="yes") for _ in range(4)]
        )
        with patch("backend.services.analysis_service.incidents_engine.record", new_callable=AsyncMock) as rec:
            _check_last_post_extraction_health("facebook", _job(), rows)
            await _drain()
            rec.assert_not_called()


async def _drain():
    # _check_last_post_extraction_health fires via asyncio.create_task --
    # give the event loop one tick so the mocked coroutine actually runs
    # before the test asserts on it.
    import asyncio
    await asyncio.sleep(0)
