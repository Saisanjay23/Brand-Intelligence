"""Structured request logs + Prometheus counters -- method/path/status/
duration only. No correlation id, no auth: the caller's own gateway
already owns both, this just keeps this process's own logs operable.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from backend.shared.logging import get_logger, log_extra
from backend.shared.metrics import (http_request_duration_seconds,
                                    http_requests_total)

log = get_logger("api.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start

        path = request.url.path
        method = request.method
        status = response.status_code

        http_requests_total.labels(method, path, str(status)).inc()
        http_request_duration_seconds.labels(method, path).observe(duration)
        log.info(
            f"{method} {path} {status} {duration * 1000:.0f}ms",
            extra=log_extra(method=method, path=path, status=status, duration_ms=round(duration * 1000, 1)),
        )
        return response
