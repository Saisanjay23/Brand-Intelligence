"""One logged-in browser session, shared by every platform and both phases.

WHAT THIS DELIBERATELY DOES NOT DO
    No canvas / WebGL / audio fingerprint spoofing, and no playwright-stealth.
    Those patches are detectable in themselves: overriding those prototypes
    reads as a privacy extension, and Facebook responds by never finishing its
    render (an infinite spinner), Twitter with "Something went wrong", and
    Instagram by not hydrating at all. Less patching survives longer here.

WHAT ACTUALLY HELPS
    * a real Google Chrome binary when one is installed -- a genuine build has
      a better reputation than bundled Chromium
    * one webdriver flag removed, and visibility forced, because a headless tab
      that reports itself hidden gets throttled
    * a stable per-platform identity: same UA, viewport, locale and timezone
      every run, because a session whose device changes daily is a session
      worth challenging
    * refusing images and fonts, which is both faster and quieter
    * pacing, which lives in human.py and matters more than any of the above
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    sys.exit("pip install playwright && playwright install chromium")

from backend.utils.logging import get_logger
from backend.stealth.human import Human

log = get_logger("browser")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

BLOCK_TYPES = {"image", "media", "font"}  # keep stylesheets: layout matters

# A 100-session pool that all present the exact same viewport is itself a
# fingerprint -- real analysts don't all run the same window size. Kept to
# common, unremarkable desktop resolutions rather than anything exotic.
# Deliberately NOT diversifying user-agent: it must keep matching the real
# Chrome major version actually installed (see chrome_binary()), and NOT
# diversifying timezone by default: without a matching per-session egress IP
# (see `proxy` below), a claimed timezone that disagrees with the real IP's
# geo is a worse tell than 100 sessions sharing one.
VIEWPORTS = [
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
    {"width": 1600, "height": 900},
    {"width": 1280, "height": 800},
]


def _pick(seed: str, pool: list):
    """Stable pick from `pool` -- same seed always lands on the same entry
    (so one session's fingerprint doesn't drift run to run), different seeds
    spread across the pool (so the whole session pool isn't identical)."""
    if not seed:
        return pool[0]
    idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(pool)
    return pool[idx]

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

# The whole init script. Two overrides, both defensible; see the module docstring.
INIT_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(document, 'visibilityState', {get: () => 'visible',
                                                    configurable: true});
Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
"""


def chrome_binary() -> str | None:
    for p in CHROME_PATHS:
        if p and Path(p).exists():
            return p
    return None


class Session:
    """A browser context carrying one account's cookies."""

    def __init__(
        self,
        options,
        cookies: list[dict],
        load_images: bool = False,
        timezone_id: str = "Asia/Kolkata",
        session_id: str = "",
        proxy: dict | None = None,
    ):
        self.o = options
        self.cookies = cookies
        self.load_images = load_images
        self.session_id = session_id
        # A proxy's own declared region is the one case where a non-default
        # timezone is safe: the claimed timezone and the real egress IP then
        # agree. `proxy` may carry an optional "timezone_id" for exactly this.
        self.timezone_id = (proxy or {}).get("timezone_id") or timezone_id
        self.proxy = proxy
        self.viewport = _pick(session_id, VIEWPORTS)
        self.human = Human()
        self.ctx = self.browser = self._pw = None

    async def start(self):
        self._pw = await async_playwright().start()
        opts = {
            "headless": not getattr(self.o, "headful", False),
            "args": LAUNCH_ARGS,
        }
        if binary := chrome_binary():
            opts["executable_path"] = binary
            log.info(f"using installed Chrome: {binary}")
        self.browser = await self._pw.chromium.launch(**opts)

        ctx_opts = {
            "user_agent": UA,
            "locale": "en-US",
            "timezone_id": self.timezone_id,
            "viewport": self.viewport,
        }
        if self.proxy and self.proxy.get("server"):
            # Playwright's per-context proxy override (Chromium only) -- lets
            # each pooled session exit through its own IP instead of every
            # session in a 100-strong pool sharing the one machine's address,
            # which is itself a correlation signal across "different" accounts.
            ctx_opts["proxy"] = {
                "server": self.proxy["server"],
                **({"username": self.proxy["username"]} if self.proxy.get("username") else {}),
                **({"password": self.proxy["password"]} if self.proxy.get("password") else {}),
            }
        self.ctx = await self.browser.new_context(**ctx_opts)
        await self.ctx.add_init_script(INIT_JS)
        from backend.utils.cookies import normalize_cookies
        safe_cookies = normalize_cookies(self.cookies)
        await self.ctx.add_cookies(safe_cookies)
        if not self.load_images:
            await self.ctx.route("**/*", self._filter)
        return self.ctx

    @staticmethod
    async def _filter(route, request):
        if request.resource_type in BLOCK_TYPES:
            await route.abort()
        else:
            await route.continue_()

    async def stop(self):
        for obj, meth in (
            (self.ctx, "close"),
            (self.browser, "close"),
            (self._pw, "stop"),
        ):
            if obj:
                try:
                    await getattr(obj, meth)()
                except Exception:
                    pass

    async def pause(self, mult: float = 1.0):
        """Between-profile pacing, jittered and fatigued."""
        scale = (
            getattr(self.o, "delay", 0) / 6.0 if getattr(self.o, "delay", 0) else 1.0
        )
        await self.human.pause("between_profiles", scale * mult)
        # Every platform's per-profile pause funnels through here, so this is
        # the one place that needs to fire for should_rest()/maybe_rest() to
        # actually do anything -- previously computed but never called.
        nap = await self.human.maybe_rest()
        if nap:
            log.info(f"human pacing: taking a {nap:.0f}s break")

    async def check_session(self, probe_url: str, login_re, checkpoint_re) -> bool:
        """Is this cookie set still logged in and unchallenged?"""
        page = await self.ctx.new_page()
        try:
            await page.goto(
                probe_url, wait_until="domcontentloaded", timeout=self.o.timeout * 1000
            )
            await page.wait_for_timeout(2500)
            body = await page.inner_text("body")
            if "/checkpoint" in page.url or checkpoint_re.search(body):
                log.error("session CHECKPOINTED -- clear it in a real browser")
                return False
            if "/login" in page.url or login_re.search(body):
                log.error("session INVALID -- cookies expired or incomplete")
                return False
            log.info(f"session valid -> {page.url}")
            return True
        finally:
            await page.close()
