# ADR 0007: A publish hold between analysis finishing and a result being client-visible

## Status
Accepted

## Context
The pipeline is discovery → analyst approval → analysis, and analysis is
triggered the instant an analyst approves a profile (`services/profile_service.py`'s
`patch_profile`, no queue). That trigger timing is fine — there's no reason
to make the analyst wait for a scrape that might turn out clean.

The gap is on the other end: the moment analysis saves a scored row
(`phase=analysis`), it was immediately visible to `GET /profiles?phase=analysis`
— the same endpoint this engine's one trusted caller (the SaaS backend,
see [ADR 0005](0005-no-auth-layer.md)) polls to pull results for its own
downstream client. If discovery matched a URL that turns out, once
analysis fills in the real signals (photo match, followers, activity,
risk score), to be a false positive, there was no window between "analysis
finished" and "the client's system may have already ingested it." An
analyst can still revert `status` after the fact (already supported), but
that doesn't undo what a downstream system already consumed.

Two shapes were considered for closing this gap:
1. A queue *before* analysis — hold a just-approved profile for N minutes,
   then start analysis. Rejected: the analyst is deciding blind at that
   point (a discovery card is just a name + avatar match), so this buys
   latency without buying a better decision. It also doesn't cover false
   positives that only become apparent from analysis's own output.
2. A hold *after* analysis, before the row is client-visible. Chosen: the
   analyst reverts with the full scored result in front of them, and
   nothing is delayed for profiles that turn out fine.

## Decision
Every profile row gets two new fields, set when analysis saves it:
`publish_hold_until` (now + `settings.publish_hold_minutes`, default 10)
and `published` (`False`). `profile_repository.find()` gained an
`include_held` parameter: when `False` (the default — used by anything
that doesn't explicitly ask otherwise, i.e. the SaaS backend's normal
poll), a `phase=analysis` query only returns rows where
`published=True OR publish_hold_until <= now OR the field predates this
change`. The analyst-facing frontend always passes `include_held=True`, so
analysts see held rows immediately (flagged, with a countdown), while
anyone else simply doesn't see them until the hold clears.

This is a filter condition evaluated lazily on read, not a state machine
transition or a background worker — nothing needs to run for a hold to
expire; the next read just returns it. `POST /profiles/{id}/publish` lets
an analyst confirm before the timer runs out for a case they're sure about.
Reverting during the hold (`PATCH status=pending|rejected`) uses the
already-existing status-change path — a rejected/pending row was never
visible to a `phase=analysis`+`status=approved` query in the first place,
hold or no hold, so no new revert logic was needed.

## Consequences
- No change to `PHASE_DISCOVERY`/`PHASE_ANALYSIS` or the rest of the
  pipeline — this is a filter and two fields on the existing document, not
  a new phase.
- Pre-existing analysis rows (no `publish_hold_until` field at all) are
  treated as already published — this only gates *newly* analysed rows
  going forward, it doesn't retroactively hide anything already live.
- The completion webhook (`services/webhook_service.py`) only ever pushed
  the job's own summary, never profile content, so it needed no change —
  a client notified "job done" that immediately polls `GET /profiles`
  during the hold window simply won't see the held rows yet; they appear
  on a later poll once the hold clears.
- `publish_hold_minutes` is one Settings field — ops can tune or disable
  (set to `0`) the window without a code change.
