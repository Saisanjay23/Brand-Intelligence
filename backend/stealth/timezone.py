"""Resolves which IANA timezone id a session's browser context should claim.

Deliberately not diversified by default: without a matching per-session
egress IP (see `stealth.proxy`), a claimed timezone that disagrees with the
real IP's geo is a worse tell than every session sharing one default. A
proxy's own declared region is the one case where a non-default timezone is
safe, the claimed timezone and the real egress IP then agree.
"""

from __future__ import annotations

from typing import Optional

DEFAULT_TIMEZONE_ID = "Asia/Kolkata"


def resolve_timezone_id(proxy: Optional[dict], default: str = DEFAULT_TIMEZONE_ID) -> str:
    return (proxy or {}).get("timezone_id") or default
