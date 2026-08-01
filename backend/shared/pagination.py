"""Shared pagination bounds.

Every list endpoint uses these for its `limit`/`offset` query params and
returns `{items, total, limit, offset}` -- one shape the Java caller only
has to parse once. There's no shared generic envelope model here: each
module defines its own concrete response shape (e.g. profiles'
`ProfilesPage`) rather than a generic `Page[T]`, since a generic added
nothing beyond what a plain `list[XOut]` return type already gives FastAPI
for OpenAPI generation.
"""

from __future__ import annotations

DEFAULT_LIMIT = 100
MAX_LIMIT = 2000
