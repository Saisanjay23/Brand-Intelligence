"""The two scoring entry points must never disagree.

`compute_score` (internal risk_score) and `compute_incident_risk_score`
(the client-facing published riskRating) were once separate
implementations of one rubric and drifted apart in production: the
incident path carried its own ACTIVE_WINDOW_DAYS = 183 against the
rubric's 180, so a profile last active 181 days ago was dormant internally
and active to the client. They now share `_rubric`; this pins that they
keep agreeing, which a shared helper makes true today but nothing else
would keep true tomorrow.

See docs/adr/0008-one-risk-rubric.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.shared.models.scoring import (
    ACTIVE_WINDOW_DAYS, compute_incident_risk_score, compute_score,
)

TODAY = datetime.now(timezone.utc).date().isoformat()
DORMANT = (datetime.now(timezone.utc) - timedelta(days=ACTIVE_WINDOW_DAYS + 20)).date().isoformat()


def _both(*, logo: bool, name: bool, location: bool, last_post: str | None, active: bool):
    internal = compute_score(logo, name, location, last_post)
    published = compute_incident_risk_score(
        has_logo=logo, has_name_match=name, followers=None,
        location="Mumbai" if location else None,
        last_post_iso=last_post, is_active=active,
    )
    return internal, published


CASES = [
    # (logo, name, location, last_post, active) -> expected
    ((True, True, True, TODAY, True), 9),
    ((True, True, False, TODAY, True), 8),
    ((True, True, True, DORMANT, False), 7),
    ((True, True, False, DORMANT, False), 7),
    ((True, True, False, None, False), 6),
    ((False, True, False, TODAY, True), 5),
    ((False, True, False, DORMANT, False), 4),
    ((False, True, False, None, False), 3),
    ((False, False, False, TODAY, True), 2),
    ((True, False, True, TODAY, True), 2),  # no name match -> floor, logo irrelevant
]


@pytest.mark.parametrize("args,expected", CASES)
def test_both_entry_points_produce_the_rubric_value(args, expected):
    logo, name, location, last_post, active = args
    internal, published = _both(
        logo=logo, name=name, location=location, last_post=last_post, active=active,
    )
    assert internal == expected
    assert published == expected


@pytest.mark.parametrize("args,_expected", CASES)
def test_the_two_never_disagree(args, _expected):
    logo, name, location, last_post, active = args
    internal, published = _both(
        logo=logo, name=name, location=location, last_post=last_post, active=active,
    )
    assert internal == published


class TestTheDriftThatActuallyHappened:
    """The 180-vs-183 gap, pinned directly."""

    @pytest.mark.parametrize("days_ago", [ACTIVE_WINDOW_DAYS + 1, ACTIVE_WINDOW_DAYS + 3])
    def test_a_post_just_past_the_window_is_dormant_on_both_sides(self, days_ago):
        last_post = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()
        internal = compute_score(True, True, False, last_post)
        # the publisher resolves is_active from the SAME constant now, so a
        # date outside the window can only ever arrive here as active=False
        published = compute_incident_risk_score(
            has_logo=True, has_name_match=True, followers=None,
            location=None, last_post_iso=last_post, is_active=False,
        )
        assert internal == published == 7

    def test_a_post_just_inside_the_window_is_active_on_both_sides(self):
        last_post = (datetime.now(timezone.utc) - timedelta(days=ACTIVE_WINDOW_DAYS - 2)).date().isoformat()
        internal = compute_score(True, True, False, last_post)
        published = compute_incident_risk_score(
            has_logo=True, has_name_match=True, followers=None,
            location=None, last_post_iso=last_post, is_active=True,
        )
        assert internal == published == 8


def test_followers_is_accepted_but_never_changes_the_score():
    base = compute_incident_risk_score(
        has_logo=True, has_name_match=True, followers=None,
        location=None, last_post_iso=TODAY, is_active=True,
    )
    for followers in (0, 1, 10_000_000):
        assert compute_incident_risk_score(
            has_logo=True, has_name_match=True, followers=followers,
            location=None, last_post_iso=TODAY, is_active=True,
        ) == base
