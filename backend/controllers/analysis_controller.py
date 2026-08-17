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

    platform = _validated_platform(body.platform) if body.platform else None

    job = job_manager.create(
        ANALYSIS, body.client_id, {"force": body.force}, platform=platform, callback_url=body.callback_url,
    )
    return {"job_id": job.id, "status": job.status}
