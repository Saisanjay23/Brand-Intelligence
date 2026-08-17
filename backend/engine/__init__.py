"""Brand Intelligence as a standalone engine: input in, results out.

Runs the same platform adapters the API path runs, with no MongoDB, no
frontend and no HTTP server, see `runner.py` for what that does and does
not include, and `credentials.py` for where logins come from without a
session pool.

    from backend.engine import DiscoveryRequest, discover, run

    result = run(discover(DiscoveryRequest(keywords=["Acme"], platforms=["twitter"])))
    for profile in result.profiles:
        print(profile["display_name"], profile["url"], profile["name_score"])

`run()` is for callers that are not already async; inside an event loop,
await `discover(...)` / `analyze(...)` directly.

The equivalent from a shell:

    python -m backend.engine platforms
    python -m backend.engine discover --keywords "Acme,Acme Corp" --out hits.json
    python -m backend.engine analyze --urls-file urls.txt --target Acme --out rows.json

Importing this package must never pull in Motor or FastAPI.
`tests_unit/test_engine_standalone.py` asserts it, because the failure
mode is silent: the import still works on a developer machine that happens
to have both installed, and only breaks on the deployment that does not.
"""

from __future__ import annotations

from backend.engine.credentials import CredentialStore, default_dir
from backend.engine.models import (
    PLATFORM_TABS,
    AnalysisRequest,
    DiscoveryRequest,
    EngineResult,
    PlatformOutcome,
    platform_for_url,
)
from backend.engine.runner import analyze, discover, run

__all__ = [
    "AnalysisRequest",
    "CredentialStore",
    "DiscoveryRequest",
    "EngineResult",
    "PLATFORM_TABS",
    "PlatformOutcome",
    "analyze",
    "default_dir",
    "discover",
    "platform_for_url",
    "run",
]
