"""Placeholder: no TLS/JA3 fingerprint control exists in this codebase.
Every session goes through a real Chromium/Chrome network stack via
Playwright, so the TLS handshake is that of a genuine browser already --
there is nothing to spoof today. Reserved for if a non-browser HTTP client
is ever introduced for a platform and needs its TLS fingerprint managed
separately.
"""

from __future__ import annotations
