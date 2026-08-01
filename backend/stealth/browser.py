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

import sys

try:
    from playwright.async_api import async_playwright
except ImportError:
    sys.exit("pip install playwright && playwright install chromium")

from backend.shared.logging import get_logger
from backend.stealth.human import Human
from backend.stealth.fingerprint import UA, VIEWPORTS, LAUNCH_ARGS, _pick, chrome_binary
from backend.stealth.navigator_spoofing import INIT_JS
from backend.stealth.proxy import build_proxy_config
from backend.stealth.timezone import resolve_timezone_id

log = get_logger("browser")

BLOCK_TYPES = {"image", "media", "font"}  # keep stylesheets: layout matters


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
        self.timezone_id = resolve_timezone_id(proxy, timezone_id)
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
        # Playwright's per-context proxy override (Chromium only).
        proxy_config = build_proxy_config(self.proxy)
        if proxy_config:
            ctx_opts["proxy"] = proxy_config
        self.ctx = await self.browser.new_context(**ctx_opts)
        await self.ctx.add_init_script(INIT_JS)
        from backend.sessions.cookies import normalize_cookies
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
