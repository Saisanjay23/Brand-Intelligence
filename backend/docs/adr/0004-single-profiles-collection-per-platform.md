# ADR 0004: One `profiles` document per profile, not separate discovery/analysis stores

## Status
Accepted (the database-topology aspect of the original title — one
collection *per platform* — was superseded by [ADR 0006](0006-single-shared-database.md);
the decision below, that discovery and analysis share one document, still
stands unchanged)

## Context
Discovery finds candidate profiles (identity only); an analyst approves
some; analysis visits and scores the approved ones. Both phases write onto
the SAME Mongo document, field-scoped so discovery never blanks analysis
fields and vice versa, with a `phase` field that only ever advances
(`discovery` → `analysis`). This is what makes a daily re-sweep idempotent
and safe to run unattended: it can never duplicate a profile, never resets
a scored profile back to "undiscovered", and an analyst's approve/reject
decision is never silently overwritten.

A "textbook" normalized schema would split this into separate `discovery`
and `analysis` collections (or even services), joined by profile id —
cleaner on paper, since discovery and analysis are conceptually distinct
use cases.

## Decision
Keep the single-document model. `db/profiles.py` defines
`DISCOVERY_FIELDS`/`ANALYSIS_FIELDS` field-ownership constants and is the
one place that performs the field-scoped upsert (`save`/`save_many`).
`engine/discovery.py` and `engine/analysis.py` are separate use cases
(separate job kinds) that both write through this same module — two
use cases over one document, not two stores.

## Consequences
- The idempotent-resweep and reconsider-on-rejected-profile-change behavior
  (see `db/profiles.py::save`) is preserved exactly, unchanged from the
  pre-rebuild system, because the write path itself is unchanged.
- One module, one place the phase-transition rule lives — actually *more*
  maintainable than two stores would be, since there's no join or
  cross-store consistency to keep correct.
- `engine/discovery.py` and `engine/analysis.py` both import `db/profiles.py`
  directly, same as every other engine module imports its db module — there
  is no ports/DI layer to route this through in the current flat
  `api → engine → db` structure.
- If a future requirement genuinely needs discovery and analysis data to
  diverge (different retention, different access patterns, different
  scaling), that's the trigger to revisit — not "it would look more
  normalized."
