"""Unit tests for the risk rubric -- pure functions, no I/O.

Pins the exact tiered cascade (see scoring.py's module docstring), and
specifically the invariant that a manual edit (`db.profiles.
compute_risk_score`/`compute_priority`) and a live scrape (`Row.risk`/
`Row.priority`) can never silently disagree -- that was a real
architectural issue in the pre-rebuild codebase (the same math lived
twice). Both now call `scoring.compute_score` directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.database.repositories.profile_repository import compute_priority, compute_risk_score
from backend.shared.models.row import Row
from backend.shared.models.scoring import BASE, MAX_SCORE, MIN_SCORE

TODAY = datetime.now(timezone.utc).date().isoformat()
OLD = (datetime.now(timezone.utc) - timedelta(days=400)).date().isoformat()


def _matched_row(**overrides) -> Row:
    row = Row(url="https://x", target="Brand", profile_name="Brand Official", name_score=95)
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


def test_floor_score_with_nothing_matched():
    row = Row(url="https://x", target="Brand")
    assert row.risk == BASE == MIN_SCORE == 2


def test_username_not_matching_stays_at_the_floor_regardless_of_other_signals():
    """Logo/location/activity are only corroborating evidence -- without a
    clear username match none of them count for anything."""
    row = Row(url="https://x", target="Brand")
    row.has_custom_pic = True
    row.location = "Mumbai"
    row.last_post_iso = TODAY
    assert row.risk == BASE == 2


def test_logo_username_location_active_is_the_max_score():
    row = _matched_row(has_custom_pic=True, location="Mumbai, India", last_post_iso=TODAY)
    assert row.risk == MAX_SCORE == 9


def test_logo_username_active_no_location_is_8():
    row = _matched_row(has_custom_pic=True, last_post_iso=TODAY)
    assert row.risk == 8


def test_logo_username_location_but_not_active_is_7():
    row = _matched_row(has_custom_pic=True, location="Mumbai, India", last_post_iso=OLD)
    assert row.risk == 7


def test_logo_username_dormant_no_location_is_also_7():
    """Location and 'posted before, just long ago' are each worth the same
    +1 -- they don't stack, but either alone is enough for tier 7."""
    row = _matched_row(has_custom_pic=True, last_post_iso=OLD)
    assert row.risk == 7


def test_logo_username_nothing_else_known_is_6():
    row = _matched_row(has_custom_pic=True)
    assert row.risk == 6


def test_username_active_no_logo_is_5():
    row = _matched_row(last_post_iso=TODAY)
    assert row.risk == 5


def test_username_dormant_no_logo_is_4():
    row = _matched_row(last_post_iso=OLD)
    assert row.risk == 4


def test_username_only_nothing_else_known_is_3():
    row = _matched_row()
    assert row.risk == 3


def test_logo_alone_sets_high_priority():
    row = Row(url="https://x", target="Brand")
    row.has_custom_pic = True
    assert row.priority == "High"


def test_name_match_without_logo_is_medium():
    row = Row(url="https://x", target="Brand")
    row.profile_name = "Brand Official"
    row.name_score = 95
    assert row.priority == "Medium"


def test_neither_logo_nor_name_is_low():
    row = Row(url="https://x", target="Brand")
    assert row.priority == "Low"


def test_blank_field_scores_nothing_never_a_penalty():
    """'Not visible to this session' must never read as evidence of
    innocence in the other direction either -- a blank stays at BASE, it
    never goes negative or below the floor."""
    row = Row(url="https://x", target="Brand")
    row.has_custom_pic = None  # unresolved, not "no"
    assert row.logo_yes == ""
    assert row.risk == BASE


def test_compute_risk_score_matches_row_risk_for_the_same_facts():
    """The manual-edit path (compute_risk_score) and the live-scrape path
    (Row.risk) must produce the identical number for the identical facts."""
    row = _matched_row(has_custom_pic=True, location="Mumbai, India", last_post_iso=OLD)

    manual = compute_risk_score(
        has_logo=row.logo_yes == "Yes", has_name_match=row.name_yes == "Yes",
        location=row.location, last_post_date=row.last_post_iso,
    )
    assert manual == row.risk == 7


def test_compute_priority_matches_row_priority():
    assert compute_priority(has_logo=True, has_name_match=False) == "High"
    assert compute_priority(has_logo=False, has_name_match=True) == "Medium"
    assert compute_priority(has_logo=False, has_name_match=False) == "Low"
