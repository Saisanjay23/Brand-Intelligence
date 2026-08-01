"""
Brand Intelligence Engine (headless)

    uvicorn backend.main:app --port 8000

Single worker, by design -- job state lives in this process's memory (see
`engine/jobs.py`). No auth, no rate limiting, no frontend: this is reached
only by your own SaaS backend over a trusted internal path, which already
owns authenticating and rate-limiting its own callers before it ever gets
here. Errors come back as FastAPI's own plain `{"detail": "message"}`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.api.middleware.request_logging import RequestLoggingMiddleware
from backend.api.routes_analysis import router as analysis_router
from backend.api.routes_clients import router as clients_router
from backend.api.routes_discovery import router as discovery_router
from backend.api.routes_health import router as health_router
from backend.api.routes_incidents import router as incidents_router
from backend.api.routes_jobs import router as jobs_router
from backend.api.routes_profiles import router as profiles_router
from backend.api.routes_sessions import router as sessions_router
from backend.db import clients as clients_db
from backend.db import incidents as incidents_db
from backend.db import profiles as profiles_db
from backend.db import sessions as sessions_db
from backend.engine import scheduler
from backend.engine import sessions as sessions_engine
from backend.engine.jobs import job_manager
from backend.shared.errors import DomainError
from backend.shared.logging import configure_logging, get_logger
from backend.shared.mongo import close as mongo_close
from backend.shared.mongo import ping as mongo_ping

configure_logging()
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if await mongo_ping():
        await clients_db.ensure_indexes()
        await profiles_db.ensure_indexes()
        await sessions_db.ensure_indexes()
        await incidents_db.ensure_indexes()
        log.info("startup: mongo reachable, indexes ensured")
        sessions_engine.start_monitor()
        scheduler.start()
    else:
        log.warning("startup: mongo unreachable -- /health/ready will report degraded")
    yield
    scheduler.stop()
    sessions_engine.stop_monitor()
    await job_manager.cancel_all()
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
    allow_origins=["*"],
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
