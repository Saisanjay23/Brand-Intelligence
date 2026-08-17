"""Business-rule validation for profile edits, beyond what the `ProfilePatch`
DTO's field types already enforce, extracted from the old
`routes_profiles.py` handler so a controller only ever calls a service, and
a service only ever calls a validator/repository, never re-implements a
status-whitelist check inline.
"""

from __future__ import annotations

from backend.shared.errors import ValidationError

STATUSES = {"pending", "approved", "rejected"}


def validate_patch_fields(fields: dict) -> dict:
    """Drop unset (None) fields, then enforce the status whitelist.
    Raises ValidationError exactly as the original inline check did."""
    if not fields:
        raise ValidationError("nothing to update")
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValidationError(f"status must be one of {', '.join(sorted(STATUSES))}")
    return fields
