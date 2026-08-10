"""Parsing post-permalink aria-label timestamps.

Facebook puts the exact publish time in each post link's aria-label for
screen readers, e.g. "Friday 7 August 2026 at 14:14" (captured live). That
is an absolute stamp, so unlike the "3d" a person sees it needs no
arithmetic against now and cannot drift as the row ages in the database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.platforms.facebook.analysis_engine import parse_aria_date


class TestRealFormats:
    def test_the_live_captured_label(self):
        assert parse_aria_date("Friday 7 August 2026 at 14:14") == "2026-08-07"

    def test_day_month_year(self):
        assert parse_aria_date("7 August 2026") == "2026-08-07"

    def test_month_day_year(self):
        assert parse_aria_date("August 7, 2026") == "2026-08-07"

    def test_abbreviated_month(self):
        assert parse_aria_date("Friday 7 Aug 2026 at 14:14") == "2026-08-07"

    def test_single_digit_day(self):
        assert parse_aria_date("Monday 3 March 2025 at 09:05") == "2025-03-03"


class TestRejects:
    def test_relative_text_is_not_a_date(self):
        # "3d" is what a human sees; it is not parseable as an absolute date
        for junk in ("3d", "2w", "Just now", "Yesterday", ""):
            assert parse_aria_date(junk) is None, junk

    def test_none_input_is_safe(self):
        assert parse_aria_date(None) is None  # type: ignore[arg-type]

    def test_a_future_date_is_rejected(self):
        # a post cannot be published in the future -- a stamp that says so is
        # a misparse, and storing it would make the profile look freshly
        # active forever
        future = datetime.now(timezone.utc) + timedelta(days=400)
        label = f"{future.day} {future.strftime('%B')} {future.year}"
        assert parse_aria_date(label) is None

    def test_a_pre_facebook_date_is_rejected(self):
        assert parse_aria_date("7 August 1999") is None

    def test_an_impossible_calendar_date_is_rejected(self):
        assert parse_aria_date("31 February 2025") is None

    def test_unknown_month_word_is_rejected(self):
        assert parse_aria_date("7 Smarch 2025") is None
