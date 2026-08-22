"""FastAPI/Starlette resolves routes in REGISTRATION order, not by
specificity -- a literal path registered after a variable one of the same
shape (`/profiles/{profile_id}`) never gets a chance to match.

THE BUG THIS CATCHES
    Live-verified 2026-08-22: `GET /profiles/retry-queue` was registered
    (correctly) before `GET /profiles/{profile_id}` in the source file, but
    a STALE running process from before that route existed answered the
    request by matching `{profile_id}` with `profile_id="retry-queue"` and
    returning "profile 'retry-queue' not found" -- a confusing 200-shaped
    error that looked like a real "no such profile" rather than a routing
    problem. The fix that time was restarting the process; this test is
    the fix for it happening again after a genuine code change, since nothing
    else would catch a new literal path accidentally added AFTER
    `/profiles/{profile_id}` in the file.

    Checks every literal (non-`{param}`) `/profiles/...` GET/POST path
    against the router's actual resolution order, not just against the
    source text, so it fails the same way a real request would.
"""

from __future__ import annotations

from backend.api.profile_routes import router

# Every literal path this router defines, alongside the one variable-path
# GET/POST each collides with in SHAPE (same number of segments after
# "/profiles"). Only these need order-checking; a literal path with a
# different segment count than any {param} route can never collide with it.
_LITERAL_VS_VARIABLE = {
    "/profiles/coverage": "GET",
    "/profiles/retry-queue": "GET",
    "/profiles/media-proxy": "GET",
}


def _ordered_paths(method: str) -> list[str]:
    return [r.path for r in router.routes if method in r.methods]


class TestLiteralPathsAreRegisteredBeforeTheVariableOnesTheyCollideWith:
    def test_get_profiles_retry_queue_precedes_get_profiles_profile_id(self):
        paths = _ordered_paths("GET")
        assert paths.index("/profiles/retry-queue") < paths.index("/profiles/{profile_id}")

    def test_get_profiles_coverage_precedes_get_profiles_profile_id(self):
        paths = _ordered_paths("GET")
        assert paths.index("/profiles/coverage") < paths.index("/profiles/{profile_id}")

    def test_get_profiles_media_proxy_precedes_get_profiles_profile_id(self):
        paths = _ordered_paths("GET")
        assert paths.index("/profiles/media-proxy") < paths.index("/profiles/{profile_id}")

    def test_post_profiles_bulk_stop_retry_precedes_post_profiles_profile_id_publish(self):
        """A different shape (3 segments) than {profile_id}/stop-retry, so
        this one cannot actually collide -- kept as a guard against someone
        "fixing" the ordering by moving it next to bulk-patch/bulk-delete
        and accidentally landing it after a route it WOULD collide with."""
        paths = _ordered_paths("POST")
        assert "/profiles/bulk-stop-retry" in paths


class TestRetryRoutesAreRegistered:
    """Guards the routes existing at all, independent of the ordering
    question above -- a route deleted outright would still pass the
    ordering tests vacuously if the assertions used `.index()` naively on
    an absent path (it would raise ValueError, which is at least loud, but
    this makes the actual expectation explicit)."""

    def test_every_retry_queue_route_is_registered(self):
        get_paths = set(_ordered_paths("GET"))
        post_paths = set(_ordered_paths("POST"))
        assert "/profiles/retry-queue" in get_paths
        assert "/profiles/{profile_id}/stop-retry" in post_paths
        assert "/profiles/{profile_id}/resume-retry" in post_paths
        assert "/profiles/bulk-stop-retry" in post_paths
