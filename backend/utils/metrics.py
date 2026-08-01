"""Prometheus metrics -- the counters/histogram every module increments
through this one module, and the `/metrics` route (registered public,
alongside `/health/*`) that exposes them in the standard text format.

Deliberately minimal: request count/latency (who's calling, how fast) and
job outcomes per platform/kind (is the actual scraping work succeeding) --
the two things an operator actually watches, not a metric for every
internal counter in the engine.
"""

from __future__ import annotations

from prometheus_client import (CONTENT_TYPE_LATEST, Counter, Histogram,
                                generate_latest)

http_requests_total = Counter(
    "bi_http_requests_total", "HTTP requests", ["method", "path", "status"]
)
http_request_duration_seconds = Histogram(
    "bi_http_request_duration_seconds", "HTTP request duration", ["method", "path"]
)
jobs_started_total = Counter(
    "bi_jobs_started_total", "Jobs started", ["platform", "kind"]
)
jobs_finished_total = Counter(
    "bi_jobs_finished_total", "Jobs reaching a terminal state", ["platform", "kind", "status"]
)


def render_latest() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
