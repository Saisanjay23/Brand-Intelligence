"""The device identity a session presents: user-agent, viewport, and the
Chromium launch flags/binary that back them up.

Deliberately NOT diversifying user-agent across random versions, it must keep
matching the real Chrome major version actually installed (`chrome_binary()`
below); a UA claiming a version that isn't the binary actually running is a worse
tell than every session sharing one. Dynamically aligned with local binaries.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
]


def chrome_binary() -> str | None:
    for p in CHROME_PATHS:
        if p and Path(p).exists():
            return p
    return None


def _detect_chrome_version(binary_path: str | None) -> tuple[str, str]:
    """Dynamically extract major and full version strings from installed Chrome."""
    default_major = "132"
    default_full = "132.0.6834.83"
    if not binary_path or not Path(binary_path).exists():
        return default_major, default_full
    try:
        if os.name == "nt":
            cmd = (
                f'powershell -NoProfile -Command "(Get-Item -LiteralPath '
                f'\'{binary_path}\').VersionInfo.ProductVersion"'
            )
            res = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=4, check=False
            )
            if match := re.search(r"(\d+)\.(\d+\.\d+\.\d+)", res.stdout):
                return match.group(1), match.group(0)
        else:
            res = subprocess.run(
                [binary_path, "--version"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if match := re.search(r"(\d+)\.(\d+\.\d+\.\d+)", res.stdout):
                return match.group(1), match.group(0)
    except Exception:
        pass
    return default_major, default_full


CHROME_MAJOR_VERSION, CHROME_FULL_VERSION = _detect_chrome_version(chrome_binary())

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{CHROME_FULL_VERSION} Safari/537.36"
)

# A 100-session pool that all present the exact same viewport is itself a
# fingerprint, real analysts don't all run the same window size. Kept to
# common, unremarkable desktop resolutions rather than anything exotic.
VIEWPORTS = [
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
    {"width": 1600, "height": 900},
    {"width": 1280, "height": 800},
]

# Stable hardware characteristics keyed by session seed so cloud/VPS server
# deployments don't leak uncharacteristically low vCPU or memory statistics.
HARDWARE_CONCURRENCY = [8, 12, 16]
DEVICE_MEMORY = [8, 16]

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
    "--no-default-browser-check",
    "--disable-site-isolation-trials",
    "--disable-features=IsolateOrigins,site-per-process",
    # keep WebRTC from advertising local addresses
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-features=WebRtcHideLocalIpsWithMdns",
]


def _pick(seed: str, pool: list):
    """Stable pick from `pool`, same seed always lands on the same entry
    (so one session's fingerprint doesn't drift run to run), different seeds
    spread across the pool (so the whole session pool isn't identical)."""
    if not seed:
        return pool[0]
    idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(pool)
    return pool[idx]


def get_identity(session_id: str = "") -> dict:
    """Returns a coherent, stable device identity for a session context."""
    return {
        "ua": UA,
        "chrome_major": CHROME_MAJOR_VERSION,
        "chrome_full": CHROME_FULL_VERSION,
        "viewport": _pick(session_id, VIEWPORTS),
        "hardware_concurrency": _pick(session_id, HARDWARE_CONCURRENCY),
        "device_memory": _pick(session_id, DEVICE_MEMORY),
    }
