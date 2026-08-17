"""The per-field extraction canary, and the pointers its alert carries.

Two separate guarantees here:

1. The detector fires when a field stops extracting across an otherwise
   healthy batch, and stays quiet on healthy batches and small ones.
2. Every code pointer it can emit resolves to a real symbol. An alert that
   names a function which no longer exists costs the reader the time to
   discover it lied, which is worse than naming nothing -- so a rename
   must fail here rather than ship.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services.analysis_service import (
    _FIELD_CANARIES,
    _LAST_POST_TARGETS,
    _check_field_extraction_health,
    _field_blank,
)
from backend.services.job_service import Job
from backend.shared.extraction import locate
from backend.shared.models.row import Row


def _job() -> Job:
    return Job(id="job1", kind="analysis", client_id="client1", platform="twitter", params={})


def _row(status="OK", name="Adani Group", followers=1000, url="https://x.com/a") -> Row:
    r = Row(url=url, target="Adani")
    r.status = status
    r.profile_name = name
    r.followers = followers
    return r


class _Mgr:
    """Stands in for JobManager; the live-feed line is presentation only."""

    def __init__(self):
        self.messages: list[str] = []

    async def emit(self, job, type_, message="", **kw):
        self.messages.append(message)


class TestPointersResolve:
    def test_every_field_canary_target_names_a_real_symbol(self):
        for platform, entries in _FIELD_CANARIES.items():
            for field, label, targets in entries:
                for target in targets:
                    resolved = locate(target)
                    assert "symbol not found" not in resolved, (
                        f"{platform}/{field} points at {target}, which no longer exists"
                    )
                    assert ".py:" in resolved, f"{target} did not resolve to a line: {resolved}"

    def test_every_last_post_target_names_a_real_symbol(self):
        for platform, targets in _LAST_POST_TARGETS.items():
            for target in targets:
                resolved = locate(target)
                assert "symbol not found" not in resolved, (
                    f"{platform} last-post points at {target}, which no longer exists"
                )

    def test_a_renamed_symbol_degrades_instead_of_raising(self):
        # the alerting path must never be what crashes a job
        out = locate("backend.platforms.twitter.analysis_engine:Scraper.no_such_function")
        assert "symbol not found" in out
        assert "backend/platforms/twitter/analysis_engine.py" in out


class TestBlankDetection:
    def test_zero_followers_is_a_real_reading_not_a_missing_field(self):
        # a brand-new impersonator account genuinely has 0 followers;
        # treating that as "extraction broken" would fire on every one
        assert _field_blank(_row(followers=0), "followers") is False

    def test_none_followers_is_missing(self):
        assert _field_blank(_row(followers=None), "followers") is True

    def test_whitespace_only_name_is_missing(self):
        assert _field_blank(_row(name="   "), "profile_name") is True


class TestFiresOnRealBreaks:
    @pytest.mark.asyncio
    async def test_majority_missing_followers_raises_an_incident(self):
        rows = [_row(followers=None) for _ in range(4)] + [_row(followers=500) for _ in range(2)]
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock) as rec:
            await _check_field_extraction_health("twitter", _job(), rows, _Mgr())
            rec.assert_called_once()
            args, kwargs = rec.call_args
            assert args[0] == "twitter"
            assert args[4] == "FieldExtractionDrift"
            assert "follower count" in args[5]
            # the alert must carry a real file:line, not a vague module name
            assert "analysis_engine.py:" in kwargs["where"]

    @pytest.mark.asyncio
    async def test_the_live_feed_is_told_too_not_just_the_email(self):
        rows = [_row(name="") for _ in range(4)] + [_row() for _ in range(2)]
        mgr = _Mgr()
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock):
            await _check_field_extraction_health("twitter", _job(), rows, mgr)
        assert any("BROKEN" in m and "display name" in m for m in mgr.messages)


class TestStaysQuietOtherwise:
    @pytest.mark.asyncio
    async def test_a_healthy_batch_raises_nothing(self):
        rows = [_row() for _ in range(8)]
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock) as rec:
            await _check_field_extraction_health("twitter", _job(), rows, _Mgr())
            rec.assert_not_called()

    @pytest.mark.asyncio
    async def test_too_few_rows_to_mean_anything(self):
        rows = [_row(followers=None) for _ in range(4)]
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock) as rec:
            await _check_field_extraction_health("twitter", _job(), rows, _Mgr())
            rec.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_minority_blank_is_just_a_few_odd_profiles(self):
        rows = [_row(followers=None) for _ in range(2)] + [_row() for _ in range(6)]
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock) as rec:
            await _check_field_extraction_health("twitter", _job(), rows, _Mgr())
            rec.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_rows_do_not_count_toward_the_signal(self):
        """A session dying mid-batch produces ERROR rows. That is a session
        problem, reported elsewhere, and must not read as parser drift."""
        rows = [_row(status="ERROR", followers=None) for _ in range(10)] + [_row() for _ in range(6)]
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock) as rec:
            await _check_field_extraction_health("twitter", _job(), rows, _Mgr())
            rec.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_platform_with_no_canaries_configured_is_a_no_op(self):
        rows = [_row(followers=None) for _ in range(8)]
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock) as rec:
            await _check_field_extraction_health("unknown-platform", _job(), rows, _Mgr())
            rec.assert_not_called()
