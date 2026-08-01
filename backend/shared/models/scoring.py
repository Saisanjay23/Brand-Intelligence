"""Risk rubric. Edit these and every platform scraper, `Row.risk`, and
`database.repositories.profile_repository.compute_risk_score` all follow.

WHY A FLOOR OF 2
    A row only reaches scoring because a sweep already matched it to the
    client's keywords, so "zero risk" is never the honest answer -- the
    candidate exists and an analyst still has to look at it.

WHY THE LOGO DOMINATES
    Anyone can register a handle containing a brand name; lifting the
    brand's actual profile photo is a deliberate act of passing-off, and it
    is what makes a fake account convincing to a victim at a glance. So the
    photo is both the heaviest single weight and the field that sets
    priority outright.

    scale: 2 (floor) .. 9 (logo + name + active + real reach)
"""

from __future__ import annotations

BASE = 2

W_LOGO = 3  # uses a real profile photo -- passing-off, the strongest signal
W_NAME = 1  # display name matches the target brand
W_ACTIVE = 2  # posted recently, so the account is live and doing harm now
W_REACH = 1  # has an audience large enough for the impersonation to spread

ACTIVE_WINDOW_DAYS = 90
NAME_THRESHOLD = 80  # token-set ratio, 0..100
REACH_AT = 1000  # followers at or above this count as real reach

MIN_SCORE = BASE
MAX_SCORE = BASE + W_LOGO + W_NAME + W_ACTIVE + W_REACH  # 9
