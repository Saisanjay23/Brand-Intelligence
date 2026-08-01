# Brand Intelligence

Two-phase impersonation triage. **Discovery** sweeps platform search for
candidate profiles; **analysis** scores each one against a risk rubric. Both
phases read the platform's own GraphQL payloads rather than scraping rendered
HTML, and write to one MongoDB document per profile.

## Layout

```
cli.py                    analysis from the command line
discover.py               discovery from the command line
backend/
  core/
    config.py             settings from env/.env — one place for every knob
    logger.py             terminal + JSONL audit log
    mongo.py              MongoDB connection handling
  db/
    clients.py, profiles.py, sessions.py, incidents.py — MongoDB models
  engine/
    jobs.py               background jobs + numbered progress events
    analysis.py           analysis engine orchestration
    discovery.py          discovery engine orchestration
    scoring.py            the risk rubric
    sessions.py           session monitor and management
  platforms/
    registry.py           which platforms exist and where sessions live
    facebook/
      session.py            what a dead Facebook session looks like
      constants.py urls.py
      discovery/            search.py (sweep) + parse.py (payload -> Hit)
      analysis/             scraper.py (visit) + readers.py (fields) + harvest.py
  api/
    routes_*.py           the whole HTTP surface, split by domain
  stealth/
    browser.py            the browser session — see "Stealth" below
    human.py              pacing: jitter, fatigue, circadian
  main.py                 ASGI app, serves the UI and API routes
frontend/src/             React + TypeScript (Vite)
session/                  one cookie file per platform — gitignored
runs/                     per-job workbook + screenshots
```

Dependencies run one way: `platforms/` imports `shared/` and `stealth/`, never
the reverse. `platforms/registry.py` loads scanners lazily, so `shared` never imports
a platform.

## Run

```bash
python -m uvicorn backend.main:app --port 8000
```

<http://127.0.0.1:8000> — discover, triage, analyse, export. Single worker:
live job state is in memory, and everything durable is in Mongo and `runs/`.

UI development with hot reload:

```bash
cd frontend && npm run dev
```

Command line, same code:

```bash
python discover.py -k "Gautam Adani" "Adani Power" -c "Adani Group"
python cli.py --target "Gautam Adani" -c "Adani Group" -i candidates.txt -o report.xlsx
```

## The workflow

1. **Discover** — keywords sweep the People and Pages tabs. Results are read
   from `SearchCometResultsPaginatedResultsQuery` edges, deduped by id, and
   stored as `phase: discovery`.
2. **Triage** — approve or reject in the grid. That decision is the analyst's
   and no sweep ever overwrites it.
3. **Analyse** — score approved (or pending) profiles. Fields land on the *same*
   document, which flips to `phase: analysis`.
4. **Export** — the DRP workbook per job.

Re-running either phase is idempotent, so a daily sweep is safe.

## Data model

One document per `(client, url)`. The two phases write **disjoint field sets**:
discovery may refresh identity, analysis owns the scored fields, and neither
touches `status`. That is what lets a re-sweep enrich a scored profile without
blanking it. `keywords` is a `$addToSet` array, so one profile found by three
keywords stays one row.

`sources` records where every field came from (`name=graphql`,
`followers=graphql-social-context`, `logo=dom-avatar`). Treat `-loose` or
`dom-` as weaker evidence than `graphql`. **A blank cell means "not visible to
this session", not "absent".**

## Extraction

Network interception first, always. The profile's own GraphQL entity — the
dicts whose `id` equals the profile id — is the only unambiguous source; the
same page also carries the notification flyout, friend suggestions and
sponsored payloads, each with their own name and follower keys. DOM reads exist
only as a labelled fallback.

Two things Facebook does not expose to an ordinary session, both confirmed by
direct search of its payloads: **join date** and, on most profiles,
**location**. Those columns stay blank rather than being guessed.

## Stealth

**This is not ban-proof, and nothing is.** It is a low-detectability posture:

- **Read-only.** No writes, likes, friend requests or messages.
- **Minimal patching.** Two init-script overrides (`navigator.webdriver`,
  visibility) and a real Chrome binary when installed. Canvas/WebGL/audio
  spoofing and `playwright-stealth` are deliberately absent — they are
  detectable in themselves and leave Facebook on an infinite spinner.
- **Stable identity.** Same UA, viewport, locale and timezone every run.
- **Pacing over everything.** `human.py` applies lognormal jitter, a fatigue
  drift and a circadian multiplier. Request rate is the lever that matters.
- **Stop on challenge.** The first checkpoint aborts the run.

No proxies, by design.

## Speed

~10s per profile in analysis, ~7s per results page in discovery. The scanner
waits for the payload it needs rather than sleeping a fixed interval, never
scrolls when the first render already carries the answer, and refuses images
and fonts.

**People search never runs out of results** — Facebook keeps serving loosely
matching profiles indefinitely — so a sweep stops on a time budget and is
reported as INCOMPLETE. Pages searches genuinely end, and report `end-of-serp`.

## Completeness

A sweep is complete only when Facebook says so (`has_next_page: false`).
Anything else — a cap, a stall, an error — is reported as incomplete
(`discover.py` exits 2). Two reconciliations guard against silent loss:

- ids Facebook **rendered** but that never parsed as an edge are backfilled
  (a layout change surfaces here instead of dropping rows);
- ids the backend **matched but never displayed** are kept as
  `processed-not-shown`. On the Pages sweep that is the difference between 30
  and 32 profiles.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | liveness + mongo reachability |
| `GET` | `/api/platforms` | scanners and session state |
| `GET` `POST` | `/api/clients` | tenants |
| `POST` | `/api/jobs/discover` | start a sweep |
| `POST` | `/api/jobs/analyse` | score urls, or everything at a status |
| `GET` | `/api/jobs`, `/api/jobs/{id}` | job state |
| `POST` | `/api/jobs/{id}/cancel` | stop a running job |
| `GET` | `/api/jobs/{id}/xlsx` | the workbook |
| `GET` | `/api/results`, `/results/stats`, `/results/urls` | stored profiles |
| `PATCH` | `/api/results/{id}` | approve/reject and analyst edits |
| `WS` | `/ws/jobs/{id}?after_seq=N` | live progress, resumable |

Progress events are numbered; reconnect with `after_seq` and the server replays
what was missed. `PATCH` is guarded by a field whitelist so it cannot rewrite
scraped evidence.

## Adding a platform

1. `backend/platforms/<name>/` with `discovery/` and `analysis/`, mirroring
   `facebook/`. Analysis exposes `start / check_session / one / run / pause /
   stop` and a `normalize_url` static method.
2. One entry in `PLATFORMS` in `platforms/registry.py`.
3. Its cookies at `session/<name>.json`.

Keep extraction in `readers.py`-style pure functions of `(row, harvest)` — no
browser, no network — so it stays testable against a saved payload.

## Debugging

- `logs/brand_intel.jsonl` — every run, one JSON object per line.
- `sources` on any row — which extractor answered.
- `GET /api/health` — is Mongo actually reachable.
- `python cli.py --check-session` — is the cookie set still alive.
- A field blank across *every* profile usually means a key moved: look in
  `platforms/<name>/analysis/constants.py` first.
