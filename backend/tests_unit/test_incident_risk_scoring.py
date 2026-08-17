"""compute_incident_risk_score (backend/shared/models/scoring.py) --
drives a published incident's client-facing riskRating. Pins the exact same
tiered rubric as test_risk_score_rubric_spec.py's compute_score (see that
file and scoring.py's module docstring for the full spec) --
these two must never drift apart again.
"""

from __future__ import annotations

from backend.shared.models.scoring import compute_incident_risk_score


def _score(**overrides) -> int:
    base = dict(
        has_logo=False, has_name_match=False, followers=None,
        location=None, last_post_iso=None, is_active=False,
    )
    base.update(overrides)
    return compute_incident_risk_score(**base)


class TestTier9ActiveFullMatch:
    def test_active_logo_and_name_match_and_location_scores_9(self):
        assert _score(is_active=True, has_logo=True, has_name_match=True, location="Mumbai") == 9

    def test_active_alone_without_both_matches_does_not_reach_9(self):
        assert _score(is_active=True, has_logo=True, has_name_match=False) != 9
        assert _score(is_active=True, has_logo=False, has_name_match=True) != 9


class TestTier8ActiveFullMatchNoLocation:
    def test_active_logo_and_name_match_no_location_scores_8(self):
        assert _score(is_active=True, has_logo=True, has_name_match=True, location=None) == 8

    def test_blank_location_string_does_not_count_as_known(self):
        # bool(location) must be the check, not location is not None
        assert _score(is_active=True, has_logo=True, has_name_match=True, location="") == 8


class TestTier7LocationOrDormant:
    def test_inactive_logo_name_and_location_scores_7(self):
        assert _score(is_active=False, has_logo=True, has_name_match=True, location="Delhi") == 7

    def test_dormant_post_no_location_scores_7(self):
        assert _score(is_active=False, has_logo=True, has_name_match=True, last_post_iso="2020-01-01", location=None) == 7

    def test_location_and_dormant_both_true_still_7_not_higher(self):
        assert _score(
            is_active=False, has_logo=True, has_name_match=True,
            last_post_iso="2020-01-01", location="Delhi",
        ) == 7


class TestTier6NoPostsAtAll:
    def test_logo_and_name_match_no_location_no_post_scores_6(self):
        assert _score(has_logo=True, has_name_match=True, is_active=False, location=None, last_post_iso=None) == 6


class TestTier5To3NameOnly:
    def test_name_match_active_no_logo_scores_5(self):
        assert _score(has_logo=False, has_name_match=True, is_active=True) == 5

    def test_name_match_dormant_no_logo_scores_4(self):
        assert _score(has_logo=False, has_name_match=True, is_active=False, last_post_iso="2020-01-01") == 4

    def test_name_match_no_posts_no_logo_scores_3(self):
        assert _score(has_logo=False, has_name_match=True, is_active=False, last_post_iso=None) == 3


class TestTier2NoNameMatchAtAll:
    def test_no_name_match_scores_2_even_with_logo(self):
        # has_name_match is the required identity signal -- a logo alone,
        # with no name match, never lifts the floor (mirrors compute_score).
        assert _score(has_logo=True, has_name_match=False) == 2

    def test_score_2_even_with_rich_metadata(self):
        assert _score(
            has_logo=False, has_name_match=False, is_active=True,
            location="Mumbai", followers=50000, last_post_iso="2026-01-01",
        ) == 2


class TestScoreIsAlwaysInRange:
    def test_every_branch_stays_within_2_to_9(self):
        import itertools
        for has_logo, has_name_match, is_active, has_location, has_followers, has_last_post in itertools.product(
            [False, True], repeat=6
        ):
            s = compute_incident_risk_score(
                has_logo=has_logo, has_name_match=has_name_match, is_active=is_active,
                location="X" if has_location else None,
                followers=100 if has_followers else None,
                last_post_iso="2026-01-01" if has_last_post else None,
            )
            assert 2 <= s <= 9
