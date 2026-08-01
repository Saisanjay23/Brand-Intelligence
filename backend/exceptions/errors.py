"""The exception hierarchy engine/db code raises against.

Engine and db code never imports FastAPI -- it raises one of these plain
exceptions, and the one handler registered in main.py turns it into
FastAPI's own default error shape, `{"detail": "message"}`, with the right
status code. No custom envelope: this engine is called internally by a
SaaS backend that already owns its own auth/error presentation to its own
callers, so there's nothing to standardize here beyond "a normal HTTP
status and a readable message."
"""

from __future__ import annotations


class DomainError(Exception):
    status_code: int = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationError(DomainError):
    status_code = 422


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    """The request is well-formed but the current state won't allow it --
    e.g. a platform session that isn't ready, a duplicate client id."""

    status_code = 409


class UpstreamPlatformError(DomainError):
    """A platform (Facebook/Instagram/...) itself misbehaved: checkpoint,
    quota, flood-wait, layout drift. Distinct from a bug in this codebase."""

    status_code = 502
