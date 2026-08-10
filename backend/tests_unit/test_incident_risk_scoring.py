"""compute_incident_risk_score (backend/shared/models/incident_scoring.py) --
the additive rubric that becomes a published incident's client-facing
riskRating. Previously had zero test coverage anywhere in the suite despite
driving a number clients actually see. Pure function, no I/O.
"""

from __future__ import annotations

from backend.shared.models.incident_scoring import compute_incident_risk_score


def _score(**overrides) -> int:
    base = dict(
        has_logo=False, username_match=False, followers=None,
        location=None, last_post_iso=None, is_active=False,
    )
    base.update(overrides)
    return compute_incident_risk_score(**base)


class TestTier9ActiveFullMatch:
    def test_active_logo_and_username_match_scores_9(self):
        assert _score(is_active=True, has_logo=True, username_match=True) == 9

    def test_location_is_irrelevant_once_active_and_fully_matched(self):
        assert _score(is_active=True, has_logo=True, username_match=True, location="Mumbai") == 9
        assert _score(is_active=True, has_logo=True, username_match=True, location=None) == 9

    def test_active_alone_without_both_matches_does_not_reach_9(self):
        assert _score(is_active=True, has_logo=True, username_match=False) != 9
        assert _score(is_active=True, has_logo=False, username_match=True) != 9


class TestTier8InactiveFullMatchWithLocation:
    def test_inactive_logo_username_and_location_scores_8(self):
        assert _score(is_active=False, has_logo=True, username_match=True, location="Delhi") == 8

    def test_blank_location_string_does_not_count_as_known(self):
        # bool(location) must be the check, not location is not None
        assert _score(is_active=False, has_logo=True, username_match=True, location="") == 7


class TestTier7InactiveFullMatchNoLocation:
    def test_inactive_logo_and_username_no_location_scores_7(self):
        assert _score(is_active=False, has_logo=True, username_match=True, location=None) == 7


class TestTier4And3PartialMatch:
    def test_logo_only_with_activity_metadata_scores_4(self):
        assert _score(has_logo=True, username_match=False, is_active=True) == 4

    def test_username_only_with_known_location_scores_4(self):
        assert _score(has_logo=False, username_match=True, location="Pune") == 4

    def test_zero_followers_still_counts_as_KNOWN_metadata_not_missing(self):
        # followers is not None, not bool(followers) -- a genuinely-zero
        # follower count is still a real, known reading and must count as
        # supporting metadata, the same distinction Row.active_yes makes
        # elsewhere for "unknown" vs "confirmed" fields
        assert _score(has_logo=True, username_match=False, followers=0) == 4

    def test_last_post_date_alone_counts_as_metadata(self):
        assert _score(has_logo=True, username_match=False, last_post_iso="2026-01-01") == 4

    def test_partial_match_with_zero_metadata_scores_3(self):
        assert _score(
            has_logo=True, username_match=False, is_active=False,
            location=None, followers=None, last_post_iso=None,
        ) == 3

    def test_username_only_no_metadata_scores_3(self):
        assert _score(has_logo=False, username_match=True) == 3


class TestTier2NoMatchAtAll:
    def test_neither_logo_nor_username_match_scores_2(self):
        assert _score(has_logo=False, username_match=False) == 2

    def test_score_2_even_with_rich_metadata(self):
        # metadata never rescues a profile with no identity match at all
        assert _score(
            has_logo=False, username_match=False, is_active=True,
            location="Mumbai", followers=50000, last_post_iso="2026-01-01",
        ) == 2


class TestScoreIsAlwaysInRange:
    def test_every_branch_stays_within_2_to_9(self):
        import itertools
        for has_logo, username_match, is_active, has_location, has_followers, has_last_post in itertools.product(
            [False, True], repeat=6
        ):
            s = compute_incident_risk_score(
                has_logo=has_logo, username_match=username_match, is_active=is_active,
                location="X" if has_location else None,
                followers=100 if has_followers else None,
                last_post_iso="2026-01-01" if has_last_post else None,
            )
            assert 2 <= s <= 9
