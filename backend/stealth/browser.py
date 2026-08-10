"""One logged-in browser session, shared by every platform and both phases.

WHAT THIS DELIBERATELY DOES NOT DO
    No canvas / WebGL / audio fingerprint spoofing, and no playwright-stealth.
    Those patches are detectable in themselves: overriding those prototypes
    reads as a privacy extension, and Facebook responds by never finishing its
    render (an infinite spinner), Twitter with "Something went wrong", and
    Instagram by not hydrating at all. Less patching survives longer here.

WHAT ACTUALLY HELPS & HAS BEEN HARDENED
    * a real Google Chrome binary when one is installed -- a genuine build has
      a better reputation than bundled Chromium
    * exact synchronization between User-Agent, Sec-CH-UA Client Hints, and JS
      runtime capabilities
    * native code masking on overrides (`webdriver`, `visibilityState`)
    * fulfilling images and fonts with valid 200 dummy payloads instead of
      aborting, preventing JS `.onerror()` alarm tracking
    * stable per-platform identity: same UA, viewport, hardware specs, locale,
      and timezone every run
    * passive Bezier pointer motion and reading micro-scrolling during checks
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
from backend.stealth.fingerprint import (
    UA,
    VIEWPORTS,
    LAUNCH_ARGS,
    _pick,
    chrome_binary,
    get_identity,
)
from backend.stealth.headers import build_extra_headers
from backend.stealth.mouse_movement import humanize_interaction
from backend.stealth.navigator_spoofing import INIT_JS, build_init_js
from backend.stealth.proxy import build_proxy_config
from backend.stealth.timezone import resolve_timezone_id

log = get_logger("browser")

BLOCK_TYPES = {"image", "media", "font"}  # keep stylesheets: layout matters

# Transparent 1x1 GIF binary to fulfill image/media requests without triggering JS .onerror
TRANSPARENT_GIF = (
    b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x05\x04\x04\x00\x00\x00"
    b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
)
# Empty binary payload for fonts to avoid triggering font load failure diagnostics
EMPTY_FONT = b"\x00\x01\x00\x00" + b"\x00" * 32


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
        self.identity = get_identity(session_id)
        self.viewport = self.identity["viewport"]
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

        locale = "en-US"
        ctx_opts = {
            "user_agent": self.identity["ua"],
            "extra_http_headers": build_extra_headers(locale=locale),
            "locale": locale,
            "timezone_id": self.timezone_id,
            "viewport": self.viewport,
        }
        # Playwright's per-context proxy override (Chromium only).
        proxy_config = build_proxy_config(self.proxy)
        if proxy_config:
            ctx_opts["proxy"] = proxy_config
        self.ctx = await self.browser.new_context(**ctx_opts)

        init_js = build_init_js(
            hardware_concurrency=self.identity["hardware_concurrency"],
            device_memory=self.identity["device_memory"],
        )
        await self.ctx.add_init_script(init_js)
        from backend.sessions.cookies import normalize_cookies

        safe_cookies = normalize_cookies(self.cookies)
        await self.ctx.add_cookies(safe_cookies)
        if not self.load_images:
            await self.ctx.route("**/*", self._filter)
        return self.ctx

    @staticmethod
    async def _filter(route, request):
        rtype = request.resource_type
        if rtype in ("image", "media"):
            await route.fulfill(
                status=200, content_type="image/gif", body=TRANSPARENT_GIF
            )
        elif rtype == "font":
            await route.fulfill(status=200, content_type="font/woff2", body=EMPTY_FONT)
        elif rtype in BLOCK_TYPES:
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

    async def interact(self, page, scroll: bool = True, moves: int = 3) -> None:
        """Executes passive human pointer motion and micro-scrolling on a page."""
        await humanize_interaction(page, scroll=scroll, moves=moves)

    async def warmup(self, page=None) -> None:
        """Executes initial orientation timing and optional benign page activity."""
        scale = (
            getattr(self.o, "delay", 0) / 6.0 if getattr(self.o, "delay", 0) else 1.0
        )
        await self.human.warmup_delay(scale=scale)
        if page and not page.is_closed():
            await self.interact(page, scroll=False, moves=2)

    async def check_session(
        self, probe_url: str, login_re, checkpoint_re, *, expect_path: str = "",
    ) -> bool:
        """Is this cookie set still logged in and unchallenged?

        `expect_path` is a POSITIVE confirmation: the path fragment the
        probe URL must still be on once the page settles. Pass it for any
        platform whose logged-out redirect does not land on an obviously
        named page.

        That parameter exists because negative-signal-only detection gave a
        confirmed false positive in production. Instagram's authenticated
        /accounts/edit/ bounces a dead session to `https://www.instagram.com/#`
        -- a URL containing neither "/login" nor "/checkpoint" -- and the
        wall it renders ("Continue", "Use another profile", "Create new
        account") matches none of the login patterns either. So a logged-out
        session was reported healthy indefinitely: the pool kept handing it
        out, every sweep using it returned nothing, and the drift canary
        then blamed the platform for changing its payload shape. Absence of
        a known failure string is not evidence of success; still being on
        the authenticated page is.
        """
        page = await self.ctx.new_page()
        try:
            await page.goto(
                probe_url, wait_until="domcontentloaded", timeout=self.o.timeout * 1000
            )
            await page.wait_for_timeout(2500)
            await self.interact(page, scroll=True, moves=2)
            # re-read AFTER settling: a client-side bounce to the login wall
            # can land after domcontentloaded, and reading the URL too early
            # sees the page we asked for rather than the one we got
            body = await page.inner_text("body")
            if "/checkpoint" in page.url or checkpoint_re.search(body):
                log.error("session CHECKPOINTED -- clear it in a real browser")
                return False
            if "/login" in page.url or login_re.search(body):
                log.error("session INVALID -- cookies expired or incomplete")
                return False
            if expect_path:
                from urllib.parse import urlparse

                landed = urlparse(page.url).path.rstrip("/")
                if expect_path.rstrip("/") not in landed:
                    log.error(
                        f"session INVALID -- redirected off {expect_path!r} to {page.url} "
                        "(authenticated page not reachable, so these cookies are not logged in)"
                    )
                    return False
            log.info(f"session valid -> {page.url}")
            return True
        finally:
            if page and not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass
