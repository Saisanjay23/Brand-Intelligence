"""Row.active_yes: the industry-standard 6-month dormant-account threshold.

BINARY BY PRODUCT DECISION. `active_yes` returns "Yes" or "No" and never a
third, empty state. "Yes" means a post inside ACTIVE_WINDOW_DAYS; anything
not shown to be inside that window is "No".

This reverses an earlier rule that returned "" whenever no date could be
scraped, on the reasoning that "we could not find out" is not the same
claim as "confirmed dormant". That reasoning still holds -- an account we
merely failed to date now reads as inactive -- and the cost is accepted
deliberately, because a blank cell in the Active column and in the client
export is not something an analyst can act on.

The distinction did not disappear, it moved: `last_post_date` is empty
exactly when the date is unknown. So "Active=No with no Last Post" is
still recognisably different from "Active=No with a date outside the
window", for anyone who needs to tell them apart.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


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


class TestThereIsNoThirdState:
    """Every path returns Yes or No -- nothing renders blank."""

    def test_no_date_and_posts_status_unknown_reads_inactive(self):
        # extraction never found a date. Previously "" (unknown); now "No",
        # with last_post_date left empty as the honest marker.
        row = Row(url="u", target="t", last_post_iso="", posts_seen="")
        assert row.active_yes == "No"

    def test_no_date_but_confirmed_zero_posts_is_a_real_no(self):
        # the one case that was always a confident "No": the account has
        # genuinely never posted
        row = Row(url="u", target="t", last_post_iso="", posts_seen="no")
        assert row.active_yes == "No"

    def test_unparseable_date_reads_inactive(self):
        row = Row(url="u", target="t", last_post_iso="not-a-date")
        assert row.active_yes == "No"

    def test_a_dated_account_still_drives_the_answer(self):
        """The collapse must not swallow the real signal: a recent date is
        still "Yes", so this is a change to the UNKNOWN case only."""
        assert _row_with_last_post(1).active_yes == "Yes"
        assert _row_with_last_post(ACTIVE_WINDOW_DAYS + 1).active_yes == "No"

    def test_active_yes_is_never_empty(self):
        for row in (
            Row(url="u", target="t"),
            Row(url="u", target="t", last_post_iso="", posts_seen=""),
            Row(url="u", target="t", last_post_iso="", posts_seen="no"),
            Row(url="u", target="t", last_post_iso="", posts_seen="yes"),
            Row(url="u", target="t", last_post_iso="garbage"),
            _row_with_last_post(0),
            _row_with_last_post(9999),
        ):
            assert row.active_yes in ("Yes", "No"), row.active_yes


class TestFeedsIntoScoringCorrectly:
    """Activity only feeds the score once the username itself is a clear
    match -- see scoring.py's tiered cascade. These rows all carry a
    matching name so the activity tier is the thing actually varying."""

    @staticmethod
    def _matched(days_ago: Optional[int]) -> Row:
        row = Row(url="u", target="Brand", profile_name="Brand Official", name_score=95)
        if days_ago is not None:
            row.last_post_iso = _row_with_last_post(days_ago).last_post_iso
        return row

    def test_active_within_six_months_outscores_dormant(self):
        active = self._matched(30)
        dormant = self._matched(200)
        assert active.risk == 5
        assert dormant.risk == 4

    def test_dormant_a_known_old_post_outscores_no_post_data_at_all(self):
        """A profile that demonstrably posted at some point, even long ago,
        is a more established impersonation than an empty shell -- so
        "dormant" is NOT collapsed to the same score as "no post data"."""
        dormant = self._matched(200)
        no_post_data = self._matched(None)
        assert dormant.risk == 4
        assert no_post_data.risk == 3
        assert dormant.risk > no_post_data.risk

    def test_unparseable_date_scores_the_same_as_no_post_data(self):
        # extraction failing to produce a usable date must not read as
        # evidence either way -- same tier as never having found one
        row = self._matched(None)
        row.last_post_iso = "not-a-date"
        assert row.risk == 3
