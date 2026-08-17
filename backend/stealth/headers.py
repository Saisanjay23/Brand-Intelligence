"""HTTP header and Client Hint (Sec-CH-UA) synchronization.

Ensures that Playwright's underlying Chromium network stack emits Sec-CH-UA
and Accept-Language headers matching the exact dynamically probed browser version
and operating system identity, preventing OS-leak detection on Linux servers.
"""

from __future__ import annotations


def build_user_agent_data(
    major_version: str = "132",
    full_version: str = "132.0.6834.83",
    platform: str = "Windows",
) -> dict:
    """Builds Playwright context user_agent_data (Sec-CH-UA Client Hints).

    Ensures HTTP headers and navigator.userAgentData match the exact browser
    build and hide underlying hosting OS environments (e.g. Docker Linux).
    """
    return {
        "brands": [
            {"brand": "Not(A:Brand", "version": "24"},
            {"brand": "Chromium", "version": str(major_version)},
            {"brand": "Google Chrome", "version": str(major_version)},
        ],
        "full_version_list": [
            {"brand": "Not(A:Brand", "version": "24.0.0.0"},
            {"brand": "Chromium", "version": str(full_version)},
            {"brand": "Google Chrome", "version": str(full_version)},
        ],
        "mobile": False,
        "platform": platform,
        "architecture": "x86",
        "platform_version": "10.0.0",
        "model": "",
    }


def build_extra_headers(locale: str = "en-US") -> dict[str, str]:
    """Ensures default navigation HTTP headers align with context locale.

    Deliberately no `Upgrade-Insecure-Requests` here. Playwright's
    `extra_http_headers` is a context-wide setting, it attaches to EVERY
    request the context makes, including cross-origin subresource fetches
    (scripts, stylesheets), not just the top-level navigation a real browser
    would send it on. Confirmed live: with it present, Facebook's CDN
    (static.xx.fbcdn.net) rejects the CORS preflight for every JS/CSS
    bundle the client-side app needs to boot ("Request header field
    upgrade-insecure-requests is not allowed by Access-Control-Allow-
    Headers"), so the page never gets past its own server-rendered loading
    splash, not after 1.5s, not after 8s, not ever. Every evidence
    screenshot this engine had captured for Facebook was that exact splash,
    byte-identical, because of this one header. Removing it: zero CORS
    errors, real page content renders immediately.
    """
    base_lang = locale.split("-")[0] if "-" in locale else locale
    if base_lang != locale:
        accept_lang = f"{locale},{base_lang};q=0.9"
    else:
        accept_lang = f"{locale};q=0.9"
    return {
        "Accept-Language": accept_lang,
    }
