"""Signals that a Facebook page is not usable.

The browser identity itself lives in backend/stealth/browser.py -- it is not
Facebook-specific, and every platform must present the same one.
"""

from __future__ import annotations

import re

RE_LOGIN = re.compile(r"(You must log in|Log in to Facebook|Log In or Sign Up)", re.I)
RE_CHECKPOINT = re.compile(
    r"(checkpoint|suspicious activity|Confirm Your Identity|"
    r"account has been locked|We've temporarily)",
    re.I,
)
RE_GONE = re.compile(
    r"(isn't available|Page Not Found|content is currently unavailable)", re.I
)
