"""Running two or more platforms in one go, from the Run hub's multi-select.

The subtle part is job locking. `job.platform` is what decides which locks
a job takes (job_service::_lock_keys), and it can only name ONE platform.
So a multi-platform run reports platform=None -- taking every platform's
lock, exactly as an "All Platforms" sweep already does -- and carries the
real selection in params, which is what scopes the sweep.

Broader locking than strictly needed is safe: it only costs concurrency.
Narrower would let two jobs drive the same logged-in session at once,
which is the thing the locks exist to prevent.
"""

from __future__ import annotations

import pytest

from backend.controllers.discovery_controller import _resolve_platforms
from backend.shared.errors import ValidationError


class TestScopeResolution:
    def test_nothing_selected_means_every_ready_platform(self):
        assert _resolve_platforms(None, None) == (None, None)
        assert _resolve_platforms([], None) == (None, None)

    def test_one_platform_collapses_to_the_single_platform_path(self):
        """Deliberate: it keeps that path's tighter per-platform locking
        and its job coalescing, instead of locking everything."""
        assert _resolve_platforms(["facebook"], None) == ("facebook", None)

    def test_two_platforms_lock_broadly_and_scope_via_params(self):
        platform, scoped = _resolve_platforms(["facebook", "twitter"], None)
        assert platform is None, "must not claim to be a single-platform job"
        assert scoped == ["facebook", "twitter"]

    def test_the_legacy_single_field_still_works(self):
        assert _resolve_platforms(None, "instagram") == ("instagram", None)

    def test_a_list_wins_over_the_single_field(self):
        platform, scoped = _resolve_platforms(["facebook", "twitter"], "instagram")
        assert platform is None
        assert scoped == ["facebook", "twitter"]

    def test_duplicates_are_collapsed_preserving_order(self):
        _, scoped = _resolve_platforms(["twitter", "facebook", "twitter"], None)
        assert scoped == ["twitter", "facebook"]

    def test_duplicates_that_reduce_to_one_take_the_single_path(self):
        assert _resolve_platforms(["facebook", "facebook"], None) == ("facebook", None)

    def test_blank_entries_are_ignored(self):
        assert _resolve_platforms(["", "  "], None) == (None, None)
        assert _resolve_platforms(["facebook", ""], None) == ("facebook", None)

    def test_an_unknown_platform_is_rejected_rather_than_silently_dropped(self):
        """A typo must not quietly become a narrower -- or wider -- sweep
        than the analyst asked for."""
        with pytest.raises(ValidationError):
            _resolve_platforms(["facebook", "myspace"], None)

    def test_a_platform_without_discovery_is_rejected(self):
        with pytest.raises(ValidationError):
            _resolve_platforms(["facebook", "not-a-platform"], None)


class TestReadinessAcceptsAList:
    @pytest.mark.asyncio
    async def test_only_the_named_platforms_are_considered(self):
        from unittest.mock import AsyncMock, patch

        from backend.services import discovery_service as svc

        checked: list[str] = []

        async def fake_state(plat):
            checked.append(plat.id)
            return "ready"

        with patch("backend.platforms.registry.session_state", fake_state):
            ready, skipped = await svc._platform_readiness(["facebook", "twitter"])

        assert sorted(ready) == ["facebook", "twitter"]
        assert skipped == {}
        assert sorted(checked) == ["facebook", "twitter"], (
            f"looked at platforms outside the selection: {checked}")

    @pytest.mark.asyncio
    async def test_a_string_still_works(self):
        from unittest.mock import AsyncMock, patch

        from backend.services import discovery_service as svc

        with patch("backend.platforms.registry.session_state",
                   new=AsyncMock(return_value="ready")):
            ready, _ = await svc._platform_readiness("instagram")
        assert ready == ["instagram"]
