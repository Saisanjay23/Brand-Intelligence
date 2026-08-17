# ADR 0009: Discovery returns what Facebook rendered, in the order it rendered it

## Status
Accepted

## Context
A Facebook search response carries two different id sets:

- `result_ids_shown` — what Facebook actually put on screen for this query.
- `processed_unicorn_ids` — ids the search backend matched and then chose
  **not** to display (deactivated, privacy-restricted, blocked to this
  viewer, region-limited, deduped, quality-filtered).

Discovery originally turned both into candidate rows, on the reasoning
that "a profile Facebook declined to show is still a candidate."

Measured against live data — 1,013 Facebook rows for one client:

| source | rows | blank |
|---|---|---|
| graphql (rendered edges) | 749 | 0 |
| processed-not-shown | 263 | 161 |
| id-backfill | 0 | — |

Every blank/numeric-id card in the UI came from the not-shown set, and no
rendered result was ever blank. Most of those ids have no viewable profile
behind them at all, which is why there was usually no name or photo to
recover — visiting them was chasing data that does not exist. In that same
dataset an analyst had already hand-rejected 113 of them: pure wasted
triage on rows that should not have existed.

Result ordering had a parallel problem. Hits were sorted by `hit.rank`
as a tiebreaker, but `rank` is per-response, not global across a sweep, so
a later page's early-ranked results interleaved ahead of an earlier page's
later-ranked ones. The visible symptom was "results start from the last
scraped page, not page 1, and don't match what a real user sees."

## Decision
**Fidelity is the contract: discovery shows exactly what a real user sees
when they run that search, in that order, and nothing more.**

- `result_ids_shown` defines the result set. `processed_unicorn_ids` is
  never turned into rows, only counted (`Sweep.unshown`, surfaced in
  `summary()`) so the gap stays observable if Facebook's filtering
  behaviour shifts.
- Rows come from exactly two sources, both things Facebook put on screen:
  a parsed rendered edge, or a rendered id that failed to parse as an edge
  (the id-backfill safety net).
- Ordering is `by_id` insertion order. Pagination is cursor-driven and
  strictly sequential, each page's edges are absorbed in render order, and
  a duplicate id on a later page is dropped rather than overwriting its
  earlier position — so natural dict order already *is* Facebook's order.
  The only sort applied is confirmed-result before best-effort backfill
  (Facebook never said where backfilled ids would have ranked), leaning on
  Python's stable sort to preserve everything else. `rank` is not used.
- A blank name on a genuinely rendered result is kept, not dropped:
  privacy-restricted profiles frequently return no name, and discarding
  them was the "people not appearing" bug. The UI's
  `entity_id`/"Unnamed Profile" fallback renders them honestly.

## Consequences
- Recall as measured against "what Facebook will show a human" is
  unchanged; recall against "every id Facebook's backend touched" is
  deliberately lower.
- Analyst triage load drops by roughly a quarter on Facebook, and the
  blank-card class of row disappears entirely.
- `Sweep.unshown` is the monitoring hook: a large shift in that count
  means Facebook changed what it filters, and is worth investigating
  before assuming discovery regressed.
