"""Row.active_yes: the industry-standard 6-month dormant-account threshold.

The critical property, and the one an incorrect implementation gets wrong
most often: an account with NO discoverable last-post date must classify as
UNKNOWN ("") -- never silently "No" (a false claim the account is dormant)
and never silently "Yes" (a false claim it's live). Only a genuine zero-post
account is a real "No".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.shared.models.row import Row
from backend.shared.models.scoring import ACTIVE_WINDOW_DAYS


def _row_with_last_post(days_ago: int) -> Row:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return Row(url="u", target="t", last_post_iso=dt.date().isoformat())


class TestSixMonthThreshold:
    def test_the_threshold_is_six_months(self):
        # 30-day months, deterministic regardless of which months the
        # window spans -- see scoring.py's own comment for why not a
        # calendar-relative "6 months ago"
        assert ACTIVE_WINDOW_DAYS == 180

    def test_a_post_today_is_active(self):
        assert _row_with_last_post(0).active_yes == "Yes"

    def test_a_post_just_inside_the_window_is_active(self):
        assert _row_with_last_post(ACTIVE_WINDOW_DAYS - 1).active_yes == "Yes"

    def test_a_post_just_past_the_window_is_inactive(self):
        assert _row_with_last_post(ACTIVE_WINDOW_DAYS + 1).active_yes == "No"

    def test_a_post_a_year_ago_is_inactive(self):
        assert _row_with_last_post(365).active_yes == "No"


class TestUnknownIsNeverCollapsedToAFalsePositive:
    def test_no_date_and_posts_status_unknown_is_UNKNOWN_not_inactive(self):
        # extraction simply never found a date -- this must not be read as
        # "confirmed dormant"
        row = Row(url="u", target="t", last_post_iso="", posts_seen="")
        assert row.active_yes == ""

    def test_no_date_but_confirmed_zero_posts_is_a_real_no(self):
        # this one IS a genuine, confirmed reading -- the account really has
        # never posted, which is different from "we couldn't find out"
        row = Row(url="u", target="t", last_post_iso="", posts_seen="no")
        assert row.active_yes == "No"

    def test_unparseable_date_is_unknown_not_a_guess(self):
        row = Row(url="u", target="t", last_post_iso="not-a-date")
        assert row.active_yes == ""


class TestFeedsIntoScoringCorrectly:
    def test_active_within_six_months_earns_the_activity_weight(self):
        from backend.shared.models.scoring import W_ACTIVE

        active = _row_with_last_post(30)
        dormant = _row_with_last_post(200)
        assert active.risk - dormant.risk == W_ACTIVE

    def test_unknown_activity_earns_no_weight_either_way(self):
        # "not visible to this session" must not be scored as either a
        # positive or negative signal -- see Row.risk's own docstring
        unknown = Row(url="u", target="t", last_post_iso="", posts_seen="")
        known_inactive = _row_with_last_post(200)
        assert unknown.risk == known_inactive.risk
