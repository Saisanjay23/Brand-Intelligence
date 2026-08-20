"""The extraction canaries must not cry wolf.

Both detectors send a CRITICAL email claiming a platform has changed its
page shape. An alert that is wrong trains an operator to ignore the next
one that is right, so a false positive here is not a cosmetic bug -- it
costs the alerting channel its credibility.

Three concrete false positives, all observed in the live incidents
collection, are pinned below:

  1. RE-ATTEMPT INFLATION. `rows` holds one entry per ATTEMPT, and the
     completeness re-attempt passes re-run only the profiles that came back
     short. Blank profiles were therefore counted up to three times while
     clean ones were counted once, which both inflated the ratio and let
     TWO blank profiles clear a five-row minimum. The live alerts read
     "6 of 6", "15 of 15" and "27 of 27" -- exactly 100%, because they were
     2, 5 and 9 profiles counted three times each.

  2. FACEBOOK GROUPS. A group publishes its member count under neither
     `followers` nor `friends` -- shared/completeness.py has always exempted
     them, the canary had not, so a batch containing the two groups in the
     database could fire "facebook stopped publishing follower counts".

  3. A genuinely bad batch must still fire. The point is accuracy, not
     silence.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services import analysis_service as svc
from backend.services.job_service import Job
from backend.shared.models.row import Row


def _job() -> Job:
    return Job(id="jX", kind="analysis", client_id="cX", platform="facebook", params={})


class _NullMgr:
    async def emit(self, *a, **k):
        return None


def _row(url: str, *, followers=None, friends=None, posts_seen="", entity_type="profile") -> Row:
    r = Row(url=url, target="Acme")
    r.status = "OK"
    r.profile_name = "Someone"
    r.followers = followers
    r.friends = friends
    r.posts_seen = posts_seen
    r.entity_type = entity_type
    return r


class TestReAttemptInflation:
    @pytest.mark.asyncio
    async def test_two_blank_profiles_retried_thrice_do_not_fire(self):
        """The live "6 of 6" alert: 2 profiles x 3 attempts. Two profiles is
        below the minimum batch this canary is supposed to require."""
        rows = [_row("https://facebook.com/x"), _row("https://facebook.com/y")] * 3
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock) as rec:
            await svc._check_field_extraction_health("facebook", _job(), rows, _NullMgr())
            svc._check_last_post_extraction_health("facebook", _job(), rows)
        assert rec.await_count == 0, "2 profiles counted 3x must not trip a critical alert"

    @pytest.mark.asyncio
    async def test_minority_of_blanks_does_not_fire_however_often_retried(self):
        """8 clean profiles, 3 blank ones re-attempted twice each. The true
        blank rate is 3/11 = 27%; counting attempts made it 9/17 = 53% and
        fired."""
        clean = [_row(f"https://facebook.com/ok{i}", followers=100, posts_seen="yes")
                 for i in range(8)]
        blank = [_row(f"https://facebook.com/bad{i}") for i in range(3)]
        rows = clean + blank + blank + blank
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock) as rec:
            await svc._check_field_extraction_health("facebook", _job(), rows, _NullMgr())
            svc._check_last_post_extraction_health("facebook", _job(), rows)
        assert rec.await_count == 0

    @pytest.mark.asyncio
    async def test_a_profile_the_retry_fixed_counts_as_read(self):
        """The last attempt is the truth: a profile that came back short and
        was filled in on re-attempt is not a miss."""
        rows = [_row(f"https://facebook.com/p{i}") for i in range(6)]
        fixed = [_row(f"https://facebook.com/p{i}", followers=500, posts_seen="yes")
                 for i in range(6)]
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock) as rec:
            await svc._check_field_extraction_health("facebook", _job(), rows + fixed, _NullMgr())
            svc._check_last_post_extraction_health("facebook", _job(), rows + fixed)
        assert rec.await_count == 0


class TestGroupsAreExempt:
    @pytest.mark.asyncio
    async def test_groups_never_trip_the_follower_canary(self):
        """Groups publish a member count under neither field, so their blank
        is structural, not drift -- the same carve-out completeness.py makes."""
        rows = [_row(f"https://facebook.com/groups/{i}/", entity_type="group",
                     posts_seen="yes") for i in range(8)]
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock) as rec:
            await svc._check_field_extraction_health("facebook", _job(), rows, _NullMgr())
        assert rec.await_count == 0


class TestGenuineDriftStillFires:
    @pytest.mark.asyncio
    async def test_a_real_batch_gone_blank_still_alerts(self):
        """The detector must keep working: 10 distinct personal profiles,
        every one loaded OK, none with any audience number."""
        rows = [_row(f"https://facebook.com/real{i}", posts_seen="yes") for i in range(10)]
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock) as rec:
            await svc._check_field_extraction_health("facebook", _job(), rows, _NullMgr())
        assert rec.await_count == 1
        assert "follower/friend count" in rec.await_args.args[5]

    @pytest.mark.asyncio
    async def test_real_last_post_drift_still_alerts(self):
        """10 distinct profiles, each read fine but none yielding either a
        post date or a confirmed 'no posts'."""
        rows = [_row(f"https://facebook.com/lp{i}", followers=50) for i in range(10)]
        with patch("backend.services.analysis_service.incidents_engine.record",
                   new_callable=AsyncMock) as rec:
            svc._check_last_post_extraction_health("facebook", _job(), rows)
            for t in list(svc._PENDING_INCIDENTS):
                await t
        assert rec.await_count == 1
