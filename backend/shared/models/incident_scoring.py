"""Risk-rating heuristic for the client-facing published-incident record
(see `services/incident_publisher.py`). Same tiered cascade as
`shared/models/scoring.py`'s `compute_score` (that one drives the tool's
own internal `risk_score`/`priority`; this one only shapes the number
written into a published incident's `riskRating` field), kept as a
separate function because the incident path's inputs are already-resolved
booleans/an `is_active` flag from `incident_publisher.py`, not a Row, but
the RUBRIC itself must never drift from the one spec both follow:

    logo match + name match:
        + location + active (posted within the activity window)   -> 9
        + active, no location                                     -> 8
        + location (any activity) OR dormant (an old post exists)  -> 7
        + neither (no location, no post date at all)               -> 6
    name match only (no logo):
        + active                                                     -> 5
        + dormant (an old post date is known)                        -> 4
        + no post date at all                                        -> 3
    no name match                                                     -> 2 (floor)

`has_name_match` is deliberately the ONLY identity signal here besides the
logo; confidence is scored off what the client actually searched for
(the same name_score/NAME_THRESHOLD comparison that drives the tool's own
internal risk_score, see shared/models/scoring.py), never off a separate
handle/username lookup. `followers` is accepted for signature parity with
callers but the spec above never uses it.

Both booleans arrive ALREADY RESOLVED, `incident_publisher.build_incident_doc`
runs the analyst's call, the validated-profile default and the scraper's
signal through `scoring.resolve_match` before calling this, so undoing a
match in the analysis view moves the published risk rating down this same
cascade rather than being quietly OR-ed back to True.
"""

from __future__ import annotations

from typing import Optional


def compute_incident_risk_score(
    *,
    has_logo: bool,
    has_name_match: bool,
    followers: Optional[int],
    location: Optional[str],
    last_post_iso: Optional[str],
    is_active: bool,
) -> int:
    if not has_name_match:
        return 2
    dormant = bool(last_post_iso) and not is_active
    if has_logo:
        if is_active:
            return 9 if location else 8
        if location or dormant:
            return 7
        return 6
    if is_active:
        return 5
    if dormant:
        return 4
    return 3
