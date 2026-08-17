"""Request shape for POST /discovery."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class DiscoveryIn(BaseModel):
    client_id: str  # must already exist, see POST /clients
    keywords: list[str] = []
    tabs: list[str] = ["people", "pages", "groups"]
    max_results: int = 0
    max_seconds: Optional[float] = None
    concurrency: Optional[int] = None
    callback_url: str = ""
    # Omitted/blank sweeps every platform with a ready session, as before
    # this is the "All Platforms" choice on the Sweep button's selector.
    # Set to one platform id (facebook/twitter/instagram/youtube/telegram)
    # to scope the sweep to just that platform; every other ready platform
    # is left untouched rather than swept alongside it. Validated against
    # the registry in discovery_controller.py, an unknown id is a 400,
    # not a silent no-op.
    platform: Optional[str] = None
    # Re-resolve name/photo for a hand-picked set of already-discovered
    # profile doc ids instead of running a fresh keyword sweep, see
    # discovery_service.py's _resweep_selected. Mutually exclusive with
    # `keywords`: when this is set, keywords/tabs/max_results are ignored.
    # Facebook only for now (the only platform with a resolve-missing-
    # name/photo step to re-run); an id for any other platform is simply
    # skipped, not an error.
    profile_ids: list[str] = []
