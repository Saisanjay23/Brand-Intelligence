# ADR 0003: API key header authentication

## Status
Superseded by [ADR 0005](0005-no-auth-layer.md)

## Context
The pre-rebuild system has no authentication at all — any caller on the
network can start a scrape, mutate results, or delete a whole tenant. At
the time this decision was made, this engine was being designed as a
standalone service reachable by any caller, so an API-key layer was added
to close that gap.

## Decision (superseded)
A shared-secret API key, sent as `X-API-Key`, validated by ASGI middleware
against a hashed key stored in Mongo. `/health/*` and `/metrics` were
exempt (probes and scrapers carry no key).

## Why it was superseded
The engine's actual integration shape turned out to be different: it is a
single internal feature bolted onto an existing SaaS product, called only
by that SaaS's own backend over a trusted internal path — never directly
by an external caller. That backend already authenticates and rate-limits
*its own* callers before ever reaching this engine. A second auth layer
here duplicated a check that was always going to happen one hop earlier,
for a caller (the SaaS backend itself) that isn't the thing needing to be
kept out. See [ADR 0005](0005-no-auth-layer.md) for the replacement
decision and what it actually removed.
