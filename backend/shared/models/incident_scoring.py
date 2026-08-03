"""Risk-rating heuristic for the client-facing published-incident record
(see `services/incident_publisher.py`). Deliberately separate from
`shared/models/scoring.py`'s `Row.risk` -- that rubric drives the tool's
own internal `risk_score`/`priority` (analyst triage, sort order, badges)
and stays untouched; this one only shapes the number written into a
published incident's `riskRating` field, per its own explicit scoring
spec.

The rules describe a strict priority cascade -- first matching tier wins,
each tier a specific combination of matched signals. Where a tier allows
two numbers depending on whether the logo also matched, the higher number
is used when the logo is present: logo evidence raises the rating within
a tier, it never lowers it.
"""

from __future__ import annotations

from typing import Optional


def compute_incident_risk_score(
    *,
    has_logo: bool,
    username_match: bool,
    followers: Optional[int],
    location: Optional[str],
    last_post_iso: Optional[str],
    is_active: bool,
) -> int:
    has_followers = followers is not None
    has_location = bool(location)
    has_last_post = bool(last_post_iso)

    if has_logo and username_match and has_followers and has_location and has_last_post and is_active:
        return 9
    if has_logo and username_match and has_followers and has_last_post and is_active:
        return 8  # everything tier-9 needs except location
    if not is_active:
        return 7 if has_logo else 6
    if not has_last_post:
        return 6 if has_logo else 5
    if not has_logo and username_match and is_active:
        return 5 if has_followers else 4
    return 3 if has_logo else 2
