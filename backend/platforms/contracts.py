"""Ports every platform adapter implements.

Application/domain code (discovery, analysis, sessions) depends only on
these Protocols, never on a concrete Playwright/MTProto/API-key class --
that is what makes "add a platform" a pure infrastructure change: implement
these three, add one registry.PLATFORMS entry, and nothing in `discovery`,
`analysis`, `jobs`, or `profiles` changes.

Structural (PEP 544) Protocols, not ABCs: every existing scraper in
`backend/platforms/*` already has exactly this shape (see the old
`registry.py`'s "Adding a platform" note: `start / check_session / one /
run / pause / stop` and a discovery `sweep`), so adapters moved over in
Phase 5 satisfy these with zero changes.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


class SessionPort(Protocol):
    """A live browser/API session for one platform, used directly by
    discovery (which needs the raw browser context) and indirectly by
    analysis/session-monitor (through ScraperPort, which owns its own)."""

    async def start(self) -> Any: ...
    async def check_session(self) -> bool: ...
    async def stop(self) -> None: ...


class ScraperPort(Protocol):
    """Analysis: profile URL -> a scored Row."""

    async def start(self) -> Any: ...
    async def check_session(self) -> bool: ...
    async def one(self, url: str, target: str, feed: str) -> Any: ...
    async def pause(self) -> None: ...
    async def stop(self) -> None: ...


class DiscovererPort(Protocol):
    """Discovery: one keyword + tab -> a completed Sweep of Hits."""

    async def sweep(self, keyword: str, tab: str, on_progress: Optional[Any] = None) -> Any: ...
