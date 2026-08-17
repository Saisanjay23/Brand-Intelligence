# ADR 0008: One risk rubric, two entry points

## Status
Accepted

## Context
The nine-tier risk cascade (logo + name + location + activity → 2..9) was
implemented twice:

- `shared/models/scoring.py::compute_score` — the tool's internal
  `risk_score`/`priority`, scoring from a last-post date.
- `shared/models/incident_scoring.py::compute_incident_risk_score` — the
  `riskRating` written onto a client-facing published incident, scoring
  from an `is_active` flag its caller had already resolved.

The split was deliberate: the two callers hold different inputs, one a
`Row` mid-scrape and one an already-resolved incident document. Both
modules' docstrings stated that the rubric itself must never diverge, and
a regression test (`test_risk_score_rubric_spec.py`) pinned the cascade
against the client's written requirement.

It diverged anyway, in the one place no test was looking — not in the
cascade, but in the constant feeding it. `scoring.py` defined
`ACTIVE_WINDOW_DAYS = 180` ("30-day months, deterministic regardless of
which months the window spans"). `services/incident_publisher.py` defined
its own `ACTIVE_WINDOW_DAYS = 183`, with a comment justifying the
difference as "distinct from the tool's own internal is_active (90-day
window)" — a value `scoring.py` has never used. So the divergence was
argued for on the basis of a stale comment that was wrong about the very
number it was contrasting against.

The effect: a profile whose last post was 181–183 days old was **dormant**
to the internal score (7 with a logo, 4 without) and **active** to the
published incident (8 with a logo, 5 without). Same profile, same moment,
two different risk ratings — the internal one an analyst sees, the
external one the client sees.

Two implementations kept in sync by discipline and documentation is what
produced this. Discipline held on the part that was tested (the cascade)
and failed on the part that was not (the threshold).

## Decision
One rubric function, `scoring.py::_rubric`, taking an already-resolved
activity tier (`active` / `dormant` / `unknown`) plus the three booleans.
Both public entry points survive unchanged in signature — callers keep the
input shape each already has — and each does nothing but resolve its tier
and delegate:

- `compute_score` derives the tier from a last-post date via
  `_activity_tier`.
- `compute_incident_risk_score` derives it from its caller's `is_active`
  flag, falling back to `dormant` when a post date exists and `unknown`
  when none does.

`incident_scoring.py` is deleted. `incident_publisher.py` imports
`ACTIVE_WINDOW_DAYS` from `scoring.py` rather than defining its own, so
the publisher's `is_active` resolution and the internal score now read the
same threshold by construction.

`followers` remains on the incident signature for caller parity; the
rubric has never used it.

## Consequences
- The 180-vs-183 gap closes on 180. Published incidents for profiles last
  active 181–183 days ago now rate one tier lower (8→7, 5→4), matching
  what the internal score has always said. This is a real, if narrow,
  change in client-facing output and is the intended correction.
- A future divergence now requires editing shared code rather than
  drifting apart passively, and `test_scoring_entry_points_agree.py`
  asserts the two entry points return the same value across the full
  cascade, including the boundary that actually broke.
- Changing the activity window is now a one-constant edit affecting both
  surfaces together, which is the property that was missing.
