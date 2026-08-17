"""Shared pagination bounds.

Every list endpoint uses these for its `limit`/`offset` query params and
returns `{items, total, limit, offset}`, one shape the Java caller only
has to parse once. There's no shared generic envelope model here: each
module defines its own concrete response shape (e.g. profiles'
`ProfilesPage`) rather than a generic `Page[T]`, since a generic added
nothing beyond what a plain `list[XOut]` return type already gives FastAPI
for OpenAPI generation.
"""

from __future__ import annotations

DEFAULT_LIMIT = 100
# Hard ceiling enforced at the API layer (see api/profile_routes.py and every
# other list endpoint). Kept at 1000, generous enough for any legitimate
# bulk caller, low enough that a runaway or malicious request cannot load
# hundreds-of-MB result sets into the worker's memory in a single query.
MAX_LIMIT = 1000
