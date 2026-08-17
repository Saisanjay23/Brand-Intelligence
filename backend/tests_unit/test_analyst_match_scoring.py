"""compute_score's `logo_match`/`username_match` kwargs -- the analyst's own
Validate-action confirmation, which now feeds the risk score exactly like
the scraper's automated has_logo/has_name_match (see shared/models/scoring.py
and database/repositories/profile_repository.py's SCORING_FIELDS).

A human's visual confirmation must count for at least as much as the
heuristic -- an analyst saying "yes, confirmed" on a profile the scraper
hasn't (yet) flagged has_logo/has_name_match for must still raise the score,
not get silently ignored.
"""

from __future__ import annotations

from backend.database.repositories.profile_repository import compute_priority, compute_risk_score
from backend.shared.models.scoring import BASE, MAX_SCORE, compute_score


class TestComputeScoreWithAnalystMatch:
    def test_analyst_match_alone_scores_the_same_as_automated_match(self):
        """logo_match/username_match=True with has_logo/has_name_match=False
        must reach the same tier as if the scraper itself had confirmed
        both -- a human's confirmation is at least as strong evidence."""
        automated = compute_score(has_logo=True, has_name_match=True, has_location=False)
        analyst_only = compute_score(
            has_logo=False, has_name_match=False, has_location=False,
            logo_match=True, username_match=True,
        )
        assert analyst_only == automated

    def test_no_match_at_all_stays_at_the_floor(self):
        assert compute_score(has_logo=False, has_name_match=False, has_location=False) == BASE

    def test_omitted_match_args_do_not_change_existing_behaviour(self):
        """Every existing caller (a live scrape has no analyst input yet)
        must score exactly as it always did -- the new kwargs default to
        None, not False, so they never accidentally suppress a match."""
        import datetime as dt
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        with_defaults = compute_score(has_logo=True, has_name_match=True, has_location=True, last_post_iso=today)
        explicit_none = compute_score(
            has_logo=True, has_name_match=True, has_location=True, last_post_iso=today,
            logo_match=None, username_match=None,
        )
        assert with_defaults == explicit_none == MAX_SCORE

    def test_username_match_alone_lifts_the_floor_like_has_name_match_does(self):
        assert compute_score(has_logo=False, has_name_match=False, has_location=False, username_match=True) > BASE


class TestProfileRepositoryScoringWithAnalystMatch:
    def test_compute_risk_score_accepts_and_uses_match_args(self):
        without = compute_risk_score(False, False, None, None)
        withit = compute_risk_score(False, False, None, None, logo_match=True, username_match=True)
        assert withit > without

    def test_compute_priority_is_high_on_logo_match_alone(self):
        assert compute_priority(False, False, logo_match=True) == "High"

    def test_compute_priority_is_medium_on_username_match_alone(self):
        assert compute_priority(False, False, username_match=True) == "Medium"

    def test_compute_priority_is_low_with_no_signal_at_all(self):
        assert compute_priority(False, False) == "Low"
