"""`POST /analysis` is the manual/catch-up trigger only, the normal path
is automatic: approving a profile in `profile_controller.py` auto-queues
analysis for that profile's own platform. This exists for an analyst who
wants to (re-)run analysis right now, either across every ready platform
or scoped to one specific platform (see `dto/analysis_dto.py`'s `platform`
field, the Re-run Analysis button's "All Platforms" vs. one-platform
selector).
"""

from __future__ import annotations

from backend.dto.analysis_dto import AnalysisIn
from backend.services import client_service
from backend.services.job_service import ANALYSIS, job_manager
from backend.services.webhook_service import validate_callback_url
from backend.shared.errors import ValidationError


def _validated_platform(platform_id: str) -> str:
    """Raises ValidationError for anything that isn't a real platform id
    unlike discovery, analysis has no `can_discover` gate: every registered
    platform always has an analysis phase (see platforms/registry.py)."""
    from backend.platforms import registry

    if platform_id not in registry.PLATFORMS:
        raise ValidationError(f"unknown platform {platform_id!r} -- known: {', '.join(registry.PLATFORMS)}")
    return platform_id


async def start_analysis(body: AnalysisIn) -> dict:
    await client_service.get(body.client_id)  # 404s if the client was never configured

    ok, reason = validate_callback_url(body.callback_url)
    if not ok:
        raise ValidationError(reason)

    if body.profile_ids:
        # A hand-picked set of profiles, not a platform/force scope -- see
        # analysis_service.py's _run_selected. platform=None: the
        # selection can span multiple platforms, so this takes every
        # platform's lock exactly as an "All Platforms" run does (see
        # discovery_controller._resolve_platforms for the same reasoning
        # on the multi-platform Run-hub case).
        job = job_manager.create(
            ANALYSIS, body.client_id, {"profile_ids": body.profile_ids},
            platform=None, callback_url=body.callback_url,
        )
        return {"job_id": job.id, "status": job.status}

    # Shared with discovery so both buttons in the Run hub scope a run the
    # same way; see discovery_controller._resolve_platforms for why a
    # multi-platform job reports platform=None.
    from backend.controllers.discovery_controller import _resolve_platforms

    platform, scoped = _resolve_platforms(body.platforms, body.platform)

    params: dict = {"force": body.force}
    if scoped:
        params["platforms"] = scoped

    job = job_manager.create(
        ANALYSIS, body.client_id, params, platform=platform, callback_url=body.callback_url,
    )
    return {"job_id": job.id, "status": job.status}
