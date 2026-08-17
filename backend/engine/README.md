# The standalone engine

Input in, results out. No MongoDB, no frontend, no HTTP server.

This package is **purely additive**. It changes nothing about the existing
API path — `backend/main.py`, the frontend, the schedulers and the Mongo
session pool all behave exactly as they did. It drives the *same* platform
adapters over the *same* contracts and hands the results back to the caller
instead of storing them.

```
                    ┌─ backend/main.py ────────► Mongo ──► frontend   (unchanged)
platforms/<name>/ ──┤
  the actual        └─ backend/engine/ ────────► your caller           (new)
  scrapers
```

## Quick start

```bash
# 1. what can actually run right now?
python -m backend.engine platforms --creds session

# 2. keywords -> candidate profiles
python -m backend.engine discover --keywords "Acme,Acme Corp" --creds session --out hits.json

# 3. profile urls -> scored rows (+ evidence screenshots)
python -m backend.engine analyze --urls-file urls.txt --target Acme --creds session --out rows.json
```

Start with `platforms`. It prints one line per platform and, for anything
not ready, the exact thing to fix:

```
  [ok ] facebook   ready      cookies
  [ok ] twitter    ready      cookies
  [-- ] youtube    missing    api-key
        -> set YOUTUBE_API_KEY, or put the key in session\youtube.key
```

## From Python

```python
from backend.engine import DiscoveryRequest, AnalysisRequest, discover, analyze, run

result = run(discover(DiscoveryRequest(keywords=["Acme"], platforms=["twitter"], max_results=20)))

print(result.ok, result.found, result.summary())
for profile in result.profiles:
    print(profile["display_name"], profile["url"], profile["name_score"])

rows = run(analyze(AnalysisRequest(
    urls=[p["url"] for p in result.profiles],
    target="Acme",
    official_feed="https://x.com/acme",
)))
```

Inside an existing event loop, `await discover(...)` directly; `run()` is
only the `asyncio.run` wrapper for synchronous callers.

## Credentials

There is no session pool without a database, so logins come from a
directory (`--creds`, `BI_CREDENTIALS_DIR`, else `credentials/` beside the
repo root):

```
<creds-dir>/
    facebook.json        a cookie export, OR this repo's own pool file
    twitter/             a directory works too — every *.json|*.txt in it
        main.json
        backup.json
    instagram.txt        Netscape cookies.txt is fine
    youtube.key          or just set YOUTUBE_API_KEY
    proxies.json         {"twitter": {"server": "..."}, "_default": {...}}
```

Accepted cookie formats: a JSON array, a Playwright `storage_state`
object, Netscape `cookies.txt`, a raw `Cookie:` header string, and this
project's own `{"version": 2, "sessions": [...]}` pool file — which is
what the repo's existing `session/*.json` files are, so `--creds session`
works out of the box and gives you every account in them as a rotation
pool. Entries already marked expired or checkpointed are skipped.

Telegram additionally needs `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` and a
Telethon `.session` file in `session/` — the interactive phone-code login
that produces it has to have been done once already.

## Output

Default is the full run as JSON on stdout; `--out` writes a file, in the
format its extension implies (`.json`, `.jsonl`, `.csv`).

Prefer `.json`: it carries the per-platform outcomes, and 40 profiles mean
something different when a platform was silently skipped for a dead
cookie.

```json
{
  "kind": "discovery", "ok": true, "found": 4, "seconds": 24.12,
  "platforms": [
    {"platform": "twitter", "status": "done", "found": 4, "session": "main@example.com"},
    {"platform": "facebook", "status": "skipped", "reason": "missing -- no cookie file"}
  ],
  "profiles": [{"platform": "twitter", "display_name": "...", "url": "...", "name_score": 100}]
}
```

Profile records use the same field names the Mongo path writes, so they
are interchangeable with anything the API already produces.

Exit codes, for a scheduler that can only see the status: `0` ran and
produced results, `1` ran but nothing usable came back, `2` bad usage,
`130` interrupted.

## When a database *is* available

`--save-to-mongo` additionally writes the run into the same `profiles`
collection the API and frontend read, keyed by `--client-id`. It is
opt-in, it happens *on top of* the normal output, and a database that has
gone away costs a warning rather than the scrape you already paid for.

## What this deliberately does not do

Each of these needs durable state that only the database provides:
cross-run deduplication, the publish hold, incident recording, the analyst
approve/reject workflow, scheduled sweeps, and least-recently-used session
rotation with persistent backoff. A standalone run is one shot — it
scrapes what you asked for and gives it to you. Within a run, sessions
still rotate on failure and results are still deduplicated.

## Invariants

`import backend.engine` must never pull in Motor, FastAPI or
`backend.database`, and the field mappings restated from the Mongo-bound
service modules must not drift from their originals. Both are enforced by
`backend/tests_unit/test_engine_standalone.py`; the first is checked in a
subprocess, because it is a failure that hides perfectly on any machine
that happens to have a database installed.
