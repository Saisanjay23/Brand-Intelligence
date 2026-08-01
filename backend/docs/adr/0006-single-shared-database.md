# ADR 0006: One shared Mongo database, not one per platform

## Status
Accepted

## Context
Earlier this database topology was one Mongo database per platform
(`brand_intel_facebook`, `brand_intel_twitter`, ...) holding that
platform's own `profiles` collection, plus one core database
(`brand_intel_core`) for everything cross-platform: orgs, clients,
sessions, incidents. Listing "every profile for this client, across
platforms" meant a fan-out query across up to six databases and merging
the results in application code.

## Decision
One database (`settings.mongo_db_name`, default `brand_intel`).
`profiles` is a single collection with a `platform` field distinguishing
which platform's adapter produced each document, instead of living in a
platform-named database. `clients`, `sessions`, `session_health`,
`session_item_health`, and `incidents` are collections in that same
database. See `docs/adr/0004` for the separate (still-standing) decision
that discovery and analysis write onto the same document rather than
separate collections — this ADR is about the database boundary, not the
document shape.

## Consequences
- "Every profile for this client" (`GET /profiles?client_id=...`) is one
  query against one collection, not a fan-out across up to six databases.
- Indexes are defined once (`db/profiles.py::ensure_indexes`) instead of
  once per platform database.
- A single unique index, `(client_id, platform, url)`, replaces what would
  otherwise need to be a unique index re-declared identically in six
  places.
- The org tier that used to sit above `client_id` in the core database is
  also gone (a caller-supplied `client_id` is the only tenancy concept
  now) — a related but separate simplification, not caused by this ADR.

## Revisit when
Per-platform data genuinely needs independent scaling, retention, or
access control that a shared collection can't express — not merely
because per-platform databases look more "separated" on paper.
