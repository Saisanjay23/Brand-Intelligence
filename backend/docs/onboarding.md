# Onboarding: a guided walkthrough of this codebase

This doc is for a developer who has never seen this repo before. Read it
top to bottom once; after that, use `architecture.md` and the ADRs as
reference. Everything here is about **how to think about the code**, not
an exhaustive listing of it.

## 1. What this system actually does

It's one feature bolted onto an existing SaaS product. That SaaS's own
backend calls this engine over plain internal HTTP to do three things:

1. **Discover** — "sweep every platform with a ready session for the
   keyword 'Acme Official'" → the engine finds candidate profiles and
   stores them with `status: pending`.
2. **Triage** — a human (or the calling platform) looks at the candidates
   and marks each `approved` or `rejected`. Approving a profile
   auto-launches analysis for that profile's own platform — no separate
   call needed.
3. **Analyse** — for every approved profile, the engine visits it for
   real, extracts followers/photo/name/location/last-post-date, and
   computes a risk score (2–9) and priority (Low/Medium/High).

The output is a scored list of profiles that are probably impersonating a
brand, stored in Mongo and served back as plain JSON — `GET /profiles`
is the one way to read it out, in one of two shapes (see below).

**The hard part is not the REST API.** The hard part is the actual
scraping: reading a platform's own GraphQL responses out of network
traffic, doing it slowly enough not to get the account banned, rotating
through a pool of logged-in sessions, and knowing when a scrape failed
because of a real problem versus a transient one. That logic lives in
`platforms/` and `stealth/`, and it has been essentially unchanged across
every restructuring this codebase has been through — this doc is about how
the code *around* that scraping logic is organized, not the scraping logic
itself.

## 2. The mental model: three flat layers, plain imports

```
api/        FastAPI routes. Unwraps a request, calls engine/, returns JSON.
engine/     All business logic. Imports db/ and platforms/ directly.
db/         ALL persistence. Nothing outside db/ imports Motor/pymongo.
```

There is no dependency-injection/ports layer and no per-module
domain/application/infrastructure split — an earlier version of this
codebase had both, justified by needing to build modules before their
dependencies existed yet. Once the whole system was being designed as one
piece in a single pass, that build-order constraint (and the ceremony it
justified) no longer applied, so it was removed. If you're looking for a
`Protocol`/port/`register_xxx` pattern from an older version of this repo,
it's gone — one module just imports the function it needs from another.

If you're ever unsure which layer something belongs in, ask: *does this
line need to know about HTTP, or about Mongo, to make sense?* If it needs
Mongo → `db/`. If it needs HTTP → `api/`. Everything else — the actual
rules (scoring, session rotation, incident diagnosis, phase transitions) —
is `engine/`, and `engine/` code never imports FastAPI or Motor directly.

## 3. Walk one real request end to end

Pick something concrete: **an analyst approves a profile, and that should
kick off an analysis job automatically.**

1. `PATCH /profiles/{id}` with `{"status": "approved"}` arrives at
   [`api/routes_profiles.py`](../api/routes_profiles.py)`::patch_profile`.
2. It validates the status value, then calls `db.profiles.patch(...)` —
   which recomputes `risk_score`/`priority` if a scoring-relevant field
   changed, and relabels provenance so a manual edit never masquerades as
   scraped evidence.
3. Back in the route handler: if the new status is `"approved"`, it calls
   `job_manager.create(ANALYSIS, updated["client_id"], {}, platform=updated["platform"])`
   directly — imported from `engine.jobs` at the top of the file. That's
   the whole "trigger" mechanism: a plain function call, not an event bus
   or a registered callback.
4. `JobManager.create()` (in [`engine/jobs.py`](../engine/jobs.py)) spawns
   a child process and returns immediately; the route handler returns the
   patched profile in the same response, `PATCH` never blocks on the scrape.

No wiring step, no composition root, no "is this registered yet" question
— `routes_profiles.py` imports `engine.jobs` the same way it imports
`db.profiles`, because there's no longer a reason it shouldn't.

## 4. How a job actually runs (the trickiest part)

A discovery or analysis job cannot run on the same process that's serving
your HTTP request — Playwright browser automation and the regex-heavy
parsing of every page it captures are CPU-heavy enough to freeze the whole
API for everyone else. So:

- `POST /discovery` or `POST /analysis` returns **immediately**
  (`202 Accepted`) with a job id. Nothing has actually started scraping
  yet at that point beyond a `multiprocessing.Process` being spawned.
- That spawned process is a **completely separate Python interpreter** —
  see `engine/jobs.py::_child_entry`. It has its own event loop, its own
  Mongo connection, its own browser. It reports progress back to the
  parent through a plain `multiprocessing.Queue` (`_ipc_queue`).
- The parent process's `JobManager._guard()` coroutine drains that queue
  and turns each message into a numbered `Event` on the `Job` object.
- You get that progress back by **polling**: `GET /jobs/{id}` for the
  current status, or `GET /jobs/{id}/events?after_seq=N` for everything
  that happened since the last time you checked. There is no WebSocket —
  see `docs/adr/0002` for why.
- If you passed `callback_url` when creating the job, the engine also
  POSTs the final result there once the job finishes
  (`engine/webhook.py::dispatch`) — a convenience on top of polling, not a
  replacement for it.

`_child_entry` itself only knows how to spawn a process and relay its
messages. The actual "what does a discovery job do" logic lives in
`engine/discovery.py::run_discovery` and `engine/analysis.py::run_analysis`
— imported *lazily*, inside `_child_entry`, specifically to avoid a
circular import (`discovery.py`/`analysis.py` need `Job`/`JobManager` from
`jobs.py` at module level; `jobs.py` needs `run_discovery`/`run_analysis`
only inside that one function). If you're trying to change what a scrape
does, that's where to look; if you're trying to change how jobs are
scheduled/cancelled/tracked, that's `jobs.py`.

## 5. Discovery sweeps every platform; the caller never names one

`POST /discovery` never takes a `platform` parameter. `engine/discovery.py::run_discovery`
calls `_ready_platforms()`, which checks every entry in
`platforms/registry.py::PLATFORMS` and keeps the ones that are `enabled`
and have a session in `state == "ready"` (checked through
`registry.session_state()`, which for cookie-backed platforms defers to
`engine/sessions.py::state_for`). It then sweeps each ready platform in
turn, inside the *same* job — one platform's session dying mid-run doesn't
abort the others; each sweep's failure is caught and noted independently
in the job's final message.

`platform` still exists internally (every profile document has one, and
it's what picks the right scraper adapter for analysis) — it's just never
a parameter the caller sets.

## 6. Adding things yourself

**Add a new field to what an analyst can edit on a profile:** add it to
`EDITABLE` in `db/profiles.py`. If it should affect the risk score, add it
to `SCORING_FIELDS` too and update `compute_risk_score`/`compute_priority`
in the same file, and add the Pydantic field to `ProfilePatch` in
`api/routes_profiles.py`. That's it — nothing else needs to change.

**Add a new platform (e.g. TikTok):** copy the shape of `platforms/facebook/`
— a `session.py`, `discovery/`, `analysis/`. Your analysis adapter needs
`start`/`check_session`/`one`/`pause`/`stop`; your discovery adapter needs
`sweep`. See `platforms/contracts.py` for the exact Protocol shapes. Add
one `Platform(...)` entry to `PLATFORMS` in `platforms/registry.py`.
Nothing in `engine/discovery.py`, `engine/analysis.py`, `engine/jobs.py`,
or `engine/sessions.py` needs to change — they're all written against the
registry, never against a concrete platform.

**Add a new endpoint:** add a route function to the relevant
`api/routes_*.py` file (or create a new one and `include_router` it in
`main.py`). If it needs business logic beyond a thin passthrough, write
that as a plain function in the matching `engine/*.py` module and call it
from the route — no interface to register, no wiring step.

## 7. Gotchas worth knowing before you hit them

- **A platform with `uses_api_key` or `env_keys` (YouTube, Telegram) has
  no session pool to rotate through.** If you write code that assumes
  `session_item` always has an `"id"` key, it will `KeyError` for these
  two platforms specifically — this was a real bug caught earlier in this
  codebase's history. Always `session_item.get("id", "")`, and if a retry
  loop's only recovery is "get a different session," check whether there
  even *is* a different session to get before looping — see
  `engine/analysis.py::_analyse_platform`'s `check_session()` handling for
  the fixed version.
- **Never write a cookie value, API key, or session identifier to a log
  line.** `engine/sessions.py::_public()` is the one function that decides
  what's safe to expose about a pooled session — route new
  session-related output through it rather than serializing a session
  dict yourself.
- **Job eviction only ever touches terminal jobs.** `JobManager._evict_old_jobs()`
  drops the oldest `DONE`/`FAILED`/`CANCELLED` jobs once `MAX_JOBS_IN_MEMORY`
  is exceeded — a `RUNNING`/`QUEUED` job is never evicted, even if that
  means briefly exceeding the cap. See `tests/test_jobs.py` for the exact
  invariants this pins.
- **The repo root `.env` is shared** between anything that reads
  `backend/shared/config.py`. If you're writing a script or a test that
  touches `write_env()` (API key save, Telegram login), be aware it
  mutates that real file — don't run it against production credentials
  casually.
- **Windows needs `taskkill /T` to kill a job's whole process tree.**
  `proc.terminate()` alone only stops the Python child, orphaning any
  browser it launched. See `engine/jobs.py::_kill_process_tree`.

## 8. Where to look next

- `docs/architecture.md` — the layer breakdown and data model.
- `docs/adr/` — the decisions that shape everything else (job model,
  polling vs. WebSocket, no auth layer, the profiles document, the shared
  database). Read these before proposing to change any of them.
- `README.md` — how to actually run it and a curl walkthrough of the full
  discover → approve → analyse → read-results flow.
