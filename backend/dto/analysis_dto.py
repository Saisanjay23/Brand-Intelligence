"""Request shape for POST /analysis."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class AnalysisIn(BaseModel):
    client_id: str
    callback_url: str = ""
    # Omitted/blank analyses every ready platform, as before, the "All
    # Platforms" choice on the Re-run Analysis button's selector. Set to one
    # platform id to scope the run to just that platform. Validated against
    # the registry in analysis_controller.py.
    platform: Optional[str] = None
    # Normally this trigger only picks up approved profiles that have never
    # been successfully analysed (or failed and are still within their
    # retry budget), see profile_repository.urls_for's exclude_analysed.
    # force=True instead re-analyses EVERY currently-approved profile for
    # the client, including ones a previous run already scored, so an
    # analyst who explicitly clicks "run analysis again" always gets a
    # fresh pass rather than the button silently doing nothing because the
    # auto-trigger-on-approve or a catch-up sweep already covered it.
    force: bool = False
