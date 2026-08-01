# Brand Intelligence Engine

Headless impersonation-triage engine, integrated as one feature of an
existing SaaS product. The SaaS backend submits a `client_id`/`client_name`/
keywords → the engine sweeps every platform with a ready session for
candidate profiles → an analyst approves/rejects → approving auto-launches
analysis → the caller polls or gets a webhook for the scored result. No
frontend, no per-caller auth — see
[`docs/architecture.md`](docs/architecture.md) and [`docs/adr/`](docs/adr/)
for the design and the reasoning behind the load-bearing decisions.

## Setup

```bash
pip install -r backend/requirements.txt
playwright install chromium
cp backend/.env.example .env
```

Fill in `.env`: at minimum `MONGO_URI`. Platform credentials
(`YOUTUBE_API_KEY`, `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`) can be set later
through the sessions API instead of by hand.

## Run

```bash
uvicorn backend.main:app --port 8000
```

Single worker, by design — see
[`docs/adr/0001-in-memory-job-orchestration.md`](docs/adr/0001-in-memory-job-orchestration.md).

`GET /docs` — interactive OpenAPI docs. `GET /health/live` / `/health/ready`
— liveness/readiness probes. `GET /metrics` — Prometheus text exposition.
No auth on any route — see
[`docs/adr/0005-no-auth-layer.md`](docs/adr/0005-no-auth-layer.md) for why.

## Typical flow

```bash
# 1. sweep every ready platform for candidates -- this also registers the
#    client (upserted from client_id/client_name/keywords) if it's new
curl -X POST localhost:8000/discovery -H "Content-Type: application/json" \
  -d '{"client_id": "acme-corp-123", "client_name": "Acme Corp", "keywords": ["Acme Official"]}'
# -> {"job_id": "...", "status": "queued"}

# 2. poll (or wait for the callback_url, if you passed one)
curl localhost:8000/jobs/{job_id}

# 3. review the triage/card list -- profile_name + logo, what a UI renders as cards
curl "localhost:8000/profiles?client_id=acme-corp-123&status=pending"

# 4. approve/reject -- approving auto-launches analysis for that profile's platform
curl -X PATCH localhost:8000/profiles/{profile_id} -H "Content-Type: application/json" \
  -d '{"status": "approved"}'

# 5. pull the scored, validated results -- client name + keyword inlined per profile
curl "localhost:8000/profiles?client_id=acme-corp-123&phase=analysis"
```

Every response is plain JSON — no export/download concept, no envelope,
errors come back as FastAPI's own `{"detail": "message"}` with a normal
HTTP status code.

## Adding a platform

1. `platforms/<name>/` with `discovery/` and `analysis/`, mirroring
   `platforms/facebook/`. The analysis adapter exposes
   `start / check_session / one / pause / stop`; discovery exposes `sweep`
   — see `platforms/contracts.py` for the exact port shapes.
2. One entry in `PLATFORMS` in `platforms/registry.py`.
3. Nothing else changes — `discovery`, `analysis`, `jobs`, and `sessions`
   are all written against the registry, not any concrete platform.

## Tests

```bash
pytest -c backend/pytest.ini
```

Unit tests cover the pure logic in `engine/` and `db/` — scoring, session
rotation, incident diagnosis, health scoring, job eviction, profile-field
conversion. No Mongo or browser needed.

## Migrating old data

Two one-off, read-only-against-the-source, idempotent scripts:

```bash
python -m backend.scripts.migrate_org_id_backfill --dry-run   # then without --dry-run
python -m backend.scripts.migrate_sessions_to_mongo --dry-run  # then without --dry-run
```

The first backfills an earlier per-org, per-platform-database schema into
the current single-database, single-`client_id` schema. The second reads
legacy `session/<platform>.json` cookie pool files into the Mongo `sessions`
collection. Neither is run automatically. See each script's docstring for
exactly what it does.
