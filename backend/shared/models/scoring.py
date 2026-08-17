"""Risk rubric. Edit these and every platform scraper, `Row.risk`, and
`database.repositories.profile_repository.compute_risk_score` all follow
both call `compute_score` below, so they can never silently disagree.

A row only reaches scoring because a sweep already matched it to the
client's keywords, so "zero risk" is never the honest answer; the
candidate exists and an analyst still has to look at it. That's the floor
of 2. Anything above it requires the username/display name to actually,
clearly match the brand (`has_name_match`), a logo, a location, or a
recent post on an account that isn't even plausibly named after the brand
is not corroborating evidence of an impersonation, it's noise.

TIERED CASCADE, NOT A FLAT WEIGHTED SUM
    logo match + username match:
        + location + active (posted within ACTIVE_WINDOW_DAYS)   -> 9
        + active, no location                                     -> 8
        + location (any activity) OR dormant (an old post exists)  -> 7
        + neither (no location, no post date at all)                -> 6
    username match only (no logo):
        + active                                                      -> 5
        + dormant (an old post date is known)                          -> 4
        + no post date at all                                           -> 3
    username does not clearly match anything above                      -> 2 (floor)

WHERE "logo match" AND "username match" COME FROM
    Both used to be asked for per-signal on the discovery card, with their
    own toggles next to Validate. They aren't any more: discovery is just
    Validate or Reject, and validating a profile means both matches hold
    for it by default (see `resolve_match`). Correcting one is an analysis
    action now, undo it there and the score moves down this cascade
    accordingly, which is the whole point of the correction.

WHY THE LOGO DOMINATES
    Anyone can register a handle containing a brand name; lifting the
    brand's actual profile photo is a deliberate act of passing-off, and it
    is what makes a fake account convincing to a victim at a glance. So a
    logo match alone is worth more than location AND dormancy-vs-none
    combined ever are (6 vs. the +1/+2 those add), and it sets priority
    outright regardless of score (see `Row.priority`).

WHY LOCATION AND "POSTED BEFORE, JUST NOT RECENTLY" DON'T STACK
    Once logo+username are already confirmed, both are secondary
    corroboration of the same kind, extra profile detail beyond a bare
    handle; so either one alone earns the same +1 tier rather than
    summing into a bigger bonus than either gives by itself. An account
    that demonstrably posted at some point (even if long ago) is treated as
    a more established, more convincing impersonation than one with zero
    visible post history, which is why dormant (7/4) outranks no-post-data
    (6/3) even though neither is "currently active".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

BASE = 2

W_NAME = 1  # a clear username/display-name match lifts the floor to 3

# 6 months, the standard dormant-account threshold used across brand-
# protection/OSINT practice: a profile that hasn't posted in that window is
# treated as inactive (lower ongoing risk, but still a real registered
# impersonation and not dismissed), while one active within it is treated
# as live and actively doing harm. 30-day months, so 180 days rather than a
# calendar-varying "6 months", deterministic regardless of which months
# the window spans.
ACTIVE_WINDOW_DAYS = 180
NAME_THRESHOLD = 80  # token-set ratio, 0..100

# The High/Medium/Low boundary used by the Match Level filter. Lives here,
# beside NAME_THRESHOLD, rather than as a literal in the query builder --
# the "High" band was once written as a separate literal in
# database/repositories/profile_repository.py and drifted to an
# unreachable byte-perfect 100, silently demoting 97 genuinely strong
# matches to Medium where nobody reviewed them. Two files agreeing by
# convention is what caused that; one definition is what prevents it.
MEDIUM_MATCH_THRESHOLD = 50

MIN_SCORE = BASE
MAX_SCORE = 9


def resolve_match(automated: bool, analyst: Optional[bool], validated: bool) -> bool:
    """Whether a logo/username match counts, from the three things that can
    say so, in strict order of authority:

    1. AN ANALYST'S EXPLICIT CALL WINS OUTRIGHT, either way. A person who
       looked at the profile and said "that is not the brand's logo"
       outranks any heuristic, so `False` here really does mean false
       it does not get OR-ed back to True by the scraper still believing
       otherwise. That OR is what used to make undoing a match in the
       analysis view leave the risk score sitting exactly where it was on
       every row the scraper had already flagged, which is precisely the
       correction an analyst is most likely to be making.
    2. OTHERWISE, A VALIDATED PROFILE COUNTS AS MATCHED. Validation is the
       analyst confirming this profile is impersonating the client, so both
       matches are the default for it and stay that way until explicitly
       undone; no separate per-signal confirmation is asked for at
       discovery time any more.
    3. OTHERWISE, whatever the scraper detected. This is every profile
       nobody has judged yet, and it scores exactly as it always did.
    """
    if analyst is not None:
        return bool(analyst)
    if validated:
        return True
    return bool(automated)


def _activity_tier(last_post_iso: Optional[str]) -> str:
    """active | dormant | unknown, from a last-post date alone.

    Deliberately does NOT need `posts_seen`, "confirmed zero posts" and
    "extraction never found a date" both mean the same thing for scoring
    purposes (no usable activity signal), they only differ for the
    display-facing `Row.active_yes` narrative.
    """
    if not last_post_iso:
        return "unknown"
    try:
        dt = datetime.strptime(last_post_iso[:10], "%Y-%m-%d")
    except ValueError:
        return "unknown"
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=ACTIVE_WINDOW_DAYS)
    return "active" if dt >= cutoff else "dormant"


def compute_score(
    has_logo: bool, has_name_match: bool, has_location: bool,
    last_post_iso: Optional[str] = "",
    *, logo_match: Optional[bool] = None, username_match: Optional[bool] = None,
    validated: bool = False,
) -> int:
    """BASE..MAX_SCORE, per the tiered cascade above. A blank/unknown field
    scores nothing, never a penalty, "not visible to this session" is not
    evidence of innocence.

    `logo_match`/`username_match` are the analyst's own call and `validated`
    is whether they have approved this profile; together with the scraped
    `has_logo`/`has_name_match` they resolve through `resolve_match` above,
    which is where the order of authority between the three is defined.
    Every argument after `last_post_iso` is keyword-only and optional, so a
    live scrape (which has no analyst input yet) scores exactly as it always
    did.
    """
    effective_logo = resolve_match(has_logo, logo_match, validated)
    effective_name_match = resolve_match(has_name_match, username_match, validated)
    if not effective_name_match:
        return BASE
    tier = _activity_tier(last_post_iso)
    if effective_logo:
        if tier == "active":
            return MAX_SCORE if has_location else MAX_SCORE - 1
        if has_location or tier == "dormant":
            return MAX_SCORE - 2
        return MAX_SCORE - 3
    if tier == "active":
        return BASE + 3
    if tier == "dormant":
        return BASE + 2
    return BASE + W_NAME
