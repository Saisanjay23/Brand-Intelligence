"""Unit tests for the risk rubric -- pure functions, no I/O.

These pin the exact rubric numbers (BASE=2, +3 logo, +1 name, +2 active,
+1 reach = max 9), and specifically the invariant that a manual edit
(`db.profiles.compute_risk_score`/`compute_priority`) and a live scrape
(`Row.risk`/`Row.priority`) can never silently disagree -- that was a real
architectural issue in the pre-rebuild codebase (the same math lived twice).
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.database.repositories.profile_repository import compute_priority, compute_risk_score
from backend.shared.models.row import Row
from backend.shared.models.scoring import BASE, MAX_SCORE, MIN_SCORE


def test_floor_score_with_nothing_matched():
    row = Row(url="https://x", target="Brand")
    assert row.risk == BASE == MIN_SCORE


def test_max_score_with_everything_matched():
    row = Row(url="https://x", target="Brand")
    row.has_custom_pic = True
    row.name_score = 100
    row.profile_name = "Brand Official"
    row.last_post_iso = datetime.now(timezone.utc).date().isoformat()  # posted today
    row.followers = 50_000
    assert row.risk == MAX_SCORE == 9


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


def test_compute_risk_score_matches_row_risk_for_the_same_booleans():
    """The manual-edit path (compute_risk_score) and the live-scrape path
    (Row.risk) must produce the identical number for the identical facts."""
    row = Row(url="https://x", target="Brand")
    row.has_custom_pic = True
    row.profile_name = "Brand Official"
    row.name_score = 90
    row.followers = 5000

    manual = compute_risk_score(
        has_logo=row.logo_yes == "Yes", has_name_match=row.name_yes == "Yes",
        is_active=row.active_yes == "Yes", followers=row.followers,
    )
    assert manual == row.risk


def test_compute_priority_matches_row_priority():
    assert compute_priority(has_logo=True, has_name_match=False) == "High"
    assert compute_priority(has_logo=False, has_name_match=True) == "Medium"
    assert compute_priority(has_logo=False, has_name_match=False) == "Low"
