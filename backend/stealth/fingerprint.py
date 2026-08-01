"""The device identity a session presents: user-agent, viewport, and the
Chromium launch flags/binary that back them up.

Deliberately NOT diversifying user-agent -- it must keep matching the real
Chrome major version actually installed (`chrome_binary()` below); a UA
claiming a version that isn't the binary actually running is a worse tell
than every session sharing one.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

# A 100-session pool that all present the exact same viewport is itself a
# fingerprint -- real analysts don't all run the same window size. Kept to
# common, unremarkable desktop resolutions rather than anything exotic.
VIEWPORTS = [
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
    {"width": 1600, "height": 900},
    {"width": 1280, "height": 800},
]

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--disable-extensions",
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
    "--no-first-run",
    "--disable-component-update",
    # keep WebRTC from advertising local addresses
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-features=WebRtcHideLocalIpsWithMdns",
]

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
]


def _pick(seed: str, pool: list):
    """Stable pick from `pool` -- same seed always lands on the same entry
    (so one session's fingerprint doesn't drift run to run), different seeds
    spread across the pool (so the whole session pool isn't identical)."""
    if not seed:
        return pool[0]
    idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(pool)
    return pool[idx]


def chrome_binary() -> str | None:
    for p in CHROME_PATHS:
        if p and Path(p).exists():
            return p
    return None
