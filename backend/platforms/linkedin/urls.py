"""LinkedIn URL shapes: normalising and identifying.

Unlike the field-reading in analysis.py/discovery.py, this is plain string
handling against a URL scheme that has been stable for years -- no live
session needed to get this right, and it needed to exist before anything
else here could be tested.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

PUBLIC_PROFILE = re.compile(r"^/in/([^/]+)/?$")
COMPANY = re.compile(r"^/company/([^/]+)/?$")


def normalize_url(url: str) -> str:
    url = (url or "").strip().strip("\"'")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    host = p.netloc.lower().split(":")[0]
    if "linkedin.com" in host:
        host = "www.linkedin.com"
    path = unquote(p.path).rstrip("/")
    return f"https://{host}{path}"


def profile_id(url: str) -> str:
    """The public identifier out of /in/<id> or /company/<id> -- LinkedIn's
    own vanity slug, not a numeric id (it doesn't expose one to a viewer)."""
    path = urlparse(normalize_url(url)).path
    for pattern in (PUBLIC_PROFILE, COMPANY):
        if m := pattern.match(path):
            return m.group(1)
    seg = [s for s in path.split("/") if s]
    return seg[-1] if seg else ""


def entity_type(url: str) -> str:
    return "page" if "/company/" in normalize_url(url) else "profile"
