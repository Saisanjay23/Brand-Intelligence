"""
Brand Intelligence Engine (headless)

    uvicorn backend.main:app --port 8000

Single worker, by design, job state lives in this process's memory (see
`services/job_service.py`). No auth, no rate limiting, no frontend: this is
reached only by your own SaaS backend over a trusted internal path, which
already owns authenticating and rate-limiting its own callers before it
ever gets here. Errors come back as FastAPI's own plain `{"detail": "message"}`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.middleware.request_logging import RequestLoggingMiddleware
from backend.api.analysis_routes import router as analysis_router
from backend.api.client_routes import router as clients_router
from backend.api.discovery_routes import router as discovery_router
from backend.api.health_routes import router as health_router
from backend.api.job_routes import router as jobs_router
from backend.api.profile_routes import router as profiles_router
from backend.api.incident_routes import router as incidents_router
from backend.api.published_incident_routes import router as published_incidents_router
from backend.api.quick_analysis_routes import router as quick_analysis_router
from backend.api.scheduler_routes import router as scheduler_router
from backend.api.session_routes import router as sessions_router
from backend.api.settings_routes import router as settings_router
from backend.database.repositories import client_repository as clients_db
from backend.database.repositories import incident_repository as incidents_db
from backend.database.repositories import profile_repository as profiles_db
from backend.database.repositories import published_incident_repository as published_incidents_db
from backend.database.repositories import session_repository as sessions_db
from backend.services import round_robin_service as round_robin
from backend.services import scheduler_service as scheduler
from backend.sessions import manager as sessions_engine
from backend.services.job_service import job_manager
from backend.config.settings import settings
from backend.shared.errors import DomainError
from backend.shared.logging import configure_logging, get_logger
from backend.database.connection import close as mongo_close
from backend.database.connection import ping as mongo_ping

configure_logging()
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if await mongo_ping():
        await clients_db.ensure_indexes()
        await profiles_db.ensure_indexes()
        await sessions_db.ensure_indexes()
        await incidents_db.ensure_indexes()
        await published_incidents_db.ensure_indexes()
        log.info("startup: mongo reachable, indexes ensured")
        # After the incident collection exists (findings are recorded as
        # incidents) and before any job can run, so a misconfigured host is
        # reported on the deploy that caused it rather than discovered later
        # in the output. Never raises; see preflight_service.run().
        from backend.services import preflight_service
        await preflight_service.run()
        from backend.platforms import registry
        for p in registry.PLATFORMS.values():
            await registry.session_state(p)
        sessions_engine.start_monitor()
        scheduler.start()
        # The round-robin engine never starts itself, on this boot or any
        # other -- by explicit product decision, discovery only ever runs
        # because an analyst was in the Scheduler tab and clicked Start
        # (POST /scheduler/start), never as a side effect of a process
        # restart nobody was watching. See round_robin_service.py's module
        # docstring.
        log.info("startup: round-robin engine stays paused until an analyst clicks Start in the Scheduler tab")
    else:
        log.warning("startup: mongo unreachable -- /health/ready will report degraded")
    yield
    round_robin.stop()
    scheduler.stop()
    sessions_engine.stop_monitor()
    job_manager.cancel_all()
    await mongo_close()


app = FastAPI(
    title="Brand Intelligence Engine",
    version="4.0.0",
    description="Headless impersonation-triage engine: discovery -> analyst "
    "approval -> scored analysis, for internal use by a SaaS backend.",
    lifespan=lifespan,
)

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    # "*" remains the default (this engine was designed for a trusted
    # internal path and the bundled UI is served same-origin), but it is
    # also what lets any page in any browser on the network drive the whole
    # API. Set CORS_ALLOW_ORIGINS to the UI's origin in staging/production.
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


app.include_router(health_router)
app.include_router(clients_router)
app.include_router(discovery_router)
app.include_router(analysis_router)
app.include_router(profiles_router)
app.include_router(sessions_router)
app.include_router(jobs_router)
app.include_router(incidents_router)
app.include_router(published_incidents_router)
app.include_router(quick_analysis_router)
app.include_router(scheduler_router)
app.include_router(settings_router)

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Every path prefix the API owns. A request under one of these that matched
# no route above is a genuine 404 and must say so as JSON.
#
# Mounting the SPA at "/" with html=True used to swallow those: an
# unmatched API path returned index.html with status 200, so a client's
# error handling never fired and the caller got an HTML parse error instead
# of the backend's own {"detail": ...}. Debugging a typo'd endpoint meant
# reading an HTML document.
_API_PREFIXES = (
    "analysis", "clients", "discovery", "health", "incidents",
    "jobs", "metrics", "profiles", "published-incidents", "quick-analysis", "scheduler", "sessions", "settings",
    "docs", "redoc", "openapi.json",
)

_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _dist.is_dir():
    # assets keep the real static mount, hashed filenames, long-lived
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="ui-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        head = full_path.split("/", 1)[0]
        if head in _API_PREFIXES:
            raise HTTPException(status_code=404, detail=f"no such endpoint: /{full_path}")
        candidate = (_dist / full_path).resolve()
        # a real file (favicon, manifest, robots.txt) is served as itself;
        # containment check because full_path is caller-supplied
        if full_path and candidate.is_file() and candidate.is_relative_to(_dist.resolve()):
            return FileResponse(str(candidate))
        # everything else is a client-side route -> the SPA shell
        return FileResponse(str(_dist / "index.html"))

