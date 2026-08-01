# Architecture

Brand Intelligence Engine (`backend/`) is a headless engine, called
internally by an existing SaaS product's own backend: submit a
`client_id`/`client_name`/keywords, the engine sweeps every platform with
a ready session for candidates, an analyst approves or rejects each one,
approving auto-launches analysis for that profile's own platform, and the
caller polls or receives a webhook for the result. No frontend, no
per-caller auth — see [ADR 0005](adr/0005-no-auth-layer.md) for why.

## Layers

Flat, three deep, plain imports — no dependency-injection ports, no
per-module domain/application/infrastructure split:

```
api/        FastAPI routes. Unwraps a request, calls engine/, returns the result.
engine/     All business logic: discovery, analysis, jobs, sessions, scheduler,
            incidents, health, alerting. Imports db/ and platforms/ directly.
db/         ALL persistence. Nothing outside db/ imports Motor/pymongo.
```

`api/` imports `engine/` and `db/`; `engine/` imports `db/` and
`platforms/`; `db/` imports nothing above it. There's exactly one
legitimate circular shape — `engine/jobs.py`'s child-process entry point
must call into `engine/discovery.py`/`engine/analysis.py`, which need
`Job`/`JobManager` from `engine/jobs.py` — resolved with a lazy import
inside the one function that needs it (`engine/jobs.py::_child_entry`),
not with a ports/DI layer. That's the only indirection in the codebase;
everywhere else, one module just imports the function it needs from
another.

## What's in each layer

| File | Owns |
|---|---|
| `db/clients.py` | Upsert/get/list/delete a client (`{client_id, name, keywords, cron}`) |
| `db/profiles.py` | The one `profiles` collection: field-scoped idempotent upsert, find, patch, stats |
| `db/sessions.py` | Pooled cookie sessions + cached health snapshots |
| `db/incidents.py` | TTL-bounded diagnosed-failure log |
| `engine/discovery.py` | Keyword sweep → candidate profiles, across every ready platform |
| `engine/analysis.py` | Approved profile URL → scraped, scored row |
| `engine/jobs.py` | `JobManager`: child-process spawn, IPC, polling state, eviction |
| `engine/sessions.py` | Pool rotation/quarantine, interactive login, background health monitor |
| `engine/scheduler.py` | Per-client cron re-sweep + analysis catch-up safety net |
| `engine/incidents.py` | Diagnoses a failure against known patterns, records + alerts |
| `engine/health.py` | Per-platform rolling health score (in-memory, operational signal) |
| `engine/alerting.py` | Incident email + daily digest |
| `platforms/*` | Six scraper adapters (unchanged scraping/extraction logic) |
| `stealth/*` | Browser fingerprinting/pacing helpers the adapters use |

`platforms/*` and `stealth/*` are infrastructure adapters carried over
from the pre-rebuild `backend/platforms/` and `backend/stealth/` — the
scraping/extraction business logic itself has never changed across any of
this session's restructurings, only where it's called from.

## Job execution

A discovery/analysis job runs in a **separate child process** (Python
`multiprocessing`), not on the API's own event loop — the scraping and its
CPU-bound JSON/regex parsing would otherwise starve every other request.
The parent process (`JobManager`) relays the child's progress back through
an IPC queue into numbered `Event`s on the `Job` object; a caller polls
`GET /jobs/{id}` or `GET /jobs/{id}/events?after_seq=N` for that, and
optionally gets a webhook POST to `callback_url` when the job reaches a
terminal state. See [ADR 0001](adr/0001-in-memory-job-orchestration.md)
and [ADR 0002](adr/0002-polling-plus-webhook-over-websocket.md).

## Data

One MongoDB database (`settings.mongo_db_name`, default `brand_intel`):
`clients`, `profiles` (one document per `(client_id, platform, url)`,
`platform` as a field — not a per-platform database), `sessions`,
`session_health`, `session_item_health`, `incidents`. See
[ADR 0006](adr/0006-single-shared-database.md) for why this isn't split
one-database-per-platform, and [ADR 0004](adr/0004-single-profiles-collection-per-platform.md)
for why discovery and analysis write the same document rather than
separate stores.

## Two response shapes off one collection

`GET /profiles` returns one of two shapes from the exact same query,
picked by the `phase` param (see `api/routes_profiles.py`):
- `phase` omitted/`discovery` → a card-sized shape for a triage UI
  (`profile_name`, `profile_image_url`, `has_logo`, `status`).
- `phase=analysis` → the full validated record, with the client's name
  and matched keyword(s) inlined per profile (fetched once per request,
  not stored redundantly on every document).

## Tenancy

No org tier. A client is `{client_id, name, keywords, created_at}`, where
`client_id` is **supplied by the caller** — the calling SaaS backend's own
customer/org id, passed straight through and upserted on, never
server-generated.

## Security

None at this layer. See [ADR 0005](adr/0005-no-auth-layer.md) — this
engine is reachable only by the SaaS backend's own trusted internal call,
which already authenticates its own end users before ever reaching here.
