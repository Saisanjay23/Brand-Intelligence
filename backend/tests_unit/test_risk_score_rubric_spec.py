"""Pins the exact risk-score rubric as specified by the client-facing
requirement (verbatim):

  logo match + username match + location + last post within 6 months        -> 9
  logo match + username match + last post within 6 months                   -> 8
  logo match + username match + (location OR last post > 6 months)          -> 7
  logo match + username match + no posts                                    -> 6
  no logo + username match + posts within last 6 months                     -> 5
  no logo + username match + posts greater than 6 months                    -> 4
  no logo + username match + no posts                                       -> 3
  (no match at all, the floor)                                              -> 2

This is a REGRESSION test, not new behaviour -- see
test_analyst_match_scoring.py for where logo_match/username_match were
folded into the pre-existing tiered cascade (shared/models/scoring.py).
Every case here already passed against that implementation; this file
exists so the rubric can never silently drift without a test failing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.shared.models.scoring import compute_score

TODAY = datetime.now(timezone.utc).date().isoformat()
OVER_6_MONTHS_AGO = (datetime.now(timezone.utc) - timedelta(days=200)).date().isoformat()


class TestRiskScoreRubric:
    def test_logo_and_username_and_location_and_recent_post_is_9(self):
        assert compute_score(
            has_logo=False, has_name_match=False, has_location=True, last_post_iso=TODAY,
            logo_match=True, username_match=True,
        ) == 9

    def test_logo_and_username_and_recent_post_no_location_is_8(self):
        assert compute_score(
            has_logo=False, has_name_match=False, has_location=False, last_post_iso=TODAY,
            logo_match=True, username_match=True,
        ) == 8

    def test_logo_and_username_and_location_but_old_post_is_7(self):
        """location present, no usable post date -- one of the two OR'd
        conditions in the spec's third rule."""
        assert compute_score(
            has_logo=False, has_name_match=False, has_location=True, last_post_iso="",
            logo_match=True, username_match=True,
        ) == 7

    def test_logo_and_username_and_dormant_post_no_location_is_7(self):
        """the other OR'd condition: no location, but a post exists and
        it's over 6 months old."""
        assert compute_score(
            has_logo=False, has_name_match=False, has_location=False, last_post_iso=OVER_6_MONTHS_AGO,
            logo_match=True, username_match=True,
        ) == 7

    def test_logo_and_username_and_location_and_dormant_post_is_still_7_not_higher(self):
        """Both OR'd conditions true at once (location present AND the
        post is over 6 months old) is still 7, not bumped up for
        satisfying two conditions instead of one -- it is an OR, not a
        count. The 9/8 tiers are reserved specifically for a RECENT
        (<=6mo) post; an old post never reaches them no matter what else
        is also true."""
        assert compute_score(
            has_logo=False, has_name_match=False, has_location=True, last_post_iso=OVER_6_MONTHS_AGO,
            logo_match=True, username_match=True,
        ) == 7

    def test_logo_and_username_and_no_posts_at_all_is_6(self):
        assert compute_score(
            has_logo=False, has_name_match=False, has_location=False, last_post_iso="",
            logo_match=True, username_match=True,
        ) == 6

    def test_no_logo_username_match_recent_post_is_5(self):
        assert compute_score(
            has_logo=False, has_name_match=False, has_location=False, last_post_iso=TODAY,
            username_match=True,
        ) == 5

    def test_no_logo_username_match_dormant_post_is_4(self):
        assert compute_score(
            has_logo=False, has_name_match=False, has_location=False, last_post_iso=OVER_6_MONTHS_AGO,
            username_match=True,
        ) == 4

    def test_no_logo_username_match_no_posts_is_3(self):
        assert compute_score(
            has_logo=False, has_name_match=False, has_location=False, last_post_iso="",
            username_match=True,
        ) == 3

    def test_no_match_at_all_is_the_floor_2(self):
        assert compute_score(has_logo=False, has_name_match=False, has_location=False, last_post_iso="") == 2

    def test_automated_has_logo_has_name_match_produce_the_identical_rubric(self):
        """The rubric is the same regardless of whether the signal came
        from the scraper (has_logo/has_name_match) or an analyst's
        Validate confirmation (logo_match/username_match) -- confirms the
        two paths were never allowed to silently disagree."""
        assert compute_score(has_logo=True, has_name_match=True, has_location=True, last_post_iso=TODAY) == 9
        assert compute_score(has_logo=True, has_name_match=True, has_location=False, last_post_iso=TODAY) == 8
        assert compute_score(has_logo=False, has_name_match=True, has_location=False, last_post_iso=OVER_6_MONTHS_AGO) == 4
