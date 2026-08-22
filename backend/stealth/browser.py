"""One logged-in browser session, shared by every platform and both phases.

WHAT THIS DELIBERATELY DOES NOT DO
    No canvas / WebGL / audio fingerprint spoofing, and no playwright-stealth.
    Those patches are detectable in themselves: overriding those prototypes
    reads as a privacy extension, and Facebook responds by never finishing its
    render (an infinite spinner), Twitter with "Something went wrong", and
    Instagram by not hydrating at all. Less patching survives longer here.

WHAT ACTUALLY HELPS & HAS BEEN HARDENED
    * a real Google Chrome binary when one is installed, a genuine build has
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
    LAUNCH_ARGS,
    chrome_binary,
    get_identity,
)
from backend.stealth.headers import build_extra_headers
from backend.stealth.mouse_movement import humanize_interaction
from backend.stealth.navigator_spoofing import build_init_js
from backend.stealth.proxy import build_proxy_config
from backend.stealth.timezone import resolve_timezone_id

log = get_logger("browser")

BLOCK_TYPES = {"media", "font"}  # keep stylesheets: layout matters

# Transparent 1x1 GIF binary to fulfill image/media requests without triggering JS .onerror
TRANSPARENT_GIF = (
    b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x05\x04\x04\x00\x00\x00"
    b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
)
# Empty binary payload for fonts to avoid triggering font load failure diagnostics
EMPTY_FONT = b"\x00\x01\x00\x00" + b"\x00" * 32

BLOCKED_TRACKERS = (
    "connect.facebook.net",
    "facebook.com/tr/",
    "analytics.twitter.com",
    "telemetry.twitter.com",
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "scorecardresearch.com",
    "tiktok.com/api/v1/web/report/",
    "adroll.com",
)


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
        await self.ctx.route("**/*", self._filter)
        return self.ctx

    async def _filter(self, route, request):
        url = request.url.lower()
        rtype = request.resource_type

        # 1. Block known third-party telemetry, ad beacons, and analytics
        if any(tracker in url for tracker in BLOCKED_TRACKERS):
            await route.fulfill(status=200, content_type="application/javascript", body=b"")
            return

        # 2. Block video/audio media streaming chunks cleanly (prevents background buffering)
        if rtype == "media":
            await route.fulfill(status=200, content_type="video/mp4", body=b"")
            return

        # 3. Block images only when evidence/image loading is disabled
        if not self.load_images and rtype == "image":
            await route.fulfill(
                status=200, content_type="image/gif", body=TRANSPARENT_GIF
            )
            return

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
        # actually do anything, previously computed but never called.
        nap = await self.human.maybe_rest()
        if nap:
            log.info(f"human pacing: taking a {nap:.0f}s break")

    async def interact(self, page, scroll: bool = True, moves: int = 3) -> None:
        """Executes passive human pointer motion and micro-scrolling on a page."""
        await humanize_interaction(page, scroll=scroll, moves=moves)

    async def wait_for_visible_content(
        self, page, min_chars: int = 200, timeout_ms: int = 4000, poll_ms: int = 250,
        *, content_selector: str = "", content_timeout_ms: int = 5000,
        settle_images_ms: int = 1500,
    ) -> None:
        """Block until the page has actually PAINTED real content, not just
        parsed enough DOM to satisfy `domcontentloaded` or a data-readiness
        check.

        The root cause of every Facebook evidence screenshot this engine
        had ever captured being the exact same byte-identical loading
        splash was `build_extra_headers()` forcing `Upgrade-Insecure-
        Requests` onto every request in the context, including cross-origin
        CDN subresources. Facebook's CDN rejected the resulting CORS
        preflight for every JS/CSS bundle its client-side app needs, so the
        page could never get past its own splash no matter how long
        anything waited (see headers.py for the fix). This wait is the
        remaining defense-in-depth once that's fixed: field extraction
        here still comes from data that can land before the screen finishes
        painting (embedded JSON script tags, intercepted API responses), so
        a slow render on an off day could still get shot mid-transition.

        Polls the page's RENDERED text via Playwright's own `inner_text()`,
        not `page.evaluate("() => document.body.innerText")`, which
        returns 0 in this headless Chromium configuration even once real
        content is on screen (confirmed live: the raw DOM property stayed
        0 for 8+ seconds on a page that Playwright's own accessor read
        correctly from frame one; `inner_text()` is what the rest of this
        codebase already uses for exactly this reason, e.g. `visit()`'s own
        `h.text[tag] = await page.inner_text("body")`). A splash screen
        carries only its own handful of characters (a logo, "from Meta")
        while any real profile page's chrome alone (nav, buttons, section
        labels) clears `min_chars` immediately. Gives up after `timeout_ms`
        and lets the caller shoot whatever is actually there rather than
        blocking evidence capture indefinitely on a profile that genuinely
        never finishes rendering.
        """
        elapsed = 0
        while elapsed < timeout_ms:
            try:
                text = await page.inner_text("body")
            except Exception:
                return  # page navigated away/closed mid-check, nothing to wait for
            if len(text) >= min_chars:
                break
            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms

        # The character floor above is necessary and NOT sufficient. Measured
        # live (2026-08-22) on real evidence captures: it is satisfied by the
        # page's own chrome long before any of the profile's content exists,
        # so every screenshot this engine produced showed a correct, complete
        # header sitting above a LOADING SPINNER where the posts belong --
        # on Instagram the whole post grid, on X the whole timeline.
        #
        # For impersonation evidence that is the wrong half of the page to
        # lose: the header proves the account copied a name and a photo, and
        # the posts are what show it is actively being used. Facebook's wait
        # returned in 0.07s for exactly this reason -- it was never waiting
        # for anything.
        #
        # `content_selector` is the platform's own hook for "a real item is
        # on screen". Bounded separately and generously, because an account
        # with genuinely no posts never satisfies it and must NOT be made to
        # pay the full timeout on every capture -- it simply shoots what is
        # there, which for that account is the truth.
        if content_selector:
            try:
                await page.wait_for_selector(
                    content_selector, timeout=content_timeout_ms, state="attached")
            except Exception:
                pass  # genuinely empty timeline, or slower than the budget

        # Give the images that are actually ON SCREEN a moment to decode.
        # A tile that exists in the DOM but has not painted screenshots as a
        # blank rectangle, which is indistinguishable in the evidence from a
        # profile that posts blank images.
        if settle_images_ms > 0:
            try:
                await page.wait_for_function(
                    """() => {
                        const vis = Array.from(document.images).filter(i => {
                          const r = i.getBoundingClientRect();
                          return r.width > 0 && r.top < innerHeight && r.bottom > 0;
                        });
                        return vis.length === 0
                            || vis.every(i => i.complete && i.naturalWidth > 0);
                    }""",
                    timeout=settle_images_ms,
                )
            except Exception:
                pass

    async def check_session(
        self, probe_url: str, login_re, checkpoint_re, *, expect_path: str = "",
        deny_paths: tuple[str, ...] = (),
    ) -> bool:
        """Is this cookie set still logged in and unchallenged?

        `expect_path` is a POSITIVE confirmation: the path fragment the
        probe URL must still be on once the page settles. Pass it for any
        platform whose logged-out redirect does not land on an obviously
        named page.

        That parameter exists because negative-signal-only detection gave a
        confirmed false positive in production. Instagram's authenticated
        /accounts/edit/ bounces a dead session to `https://www.instagram.com/#`
       , a URL containing neither "/login" nor "/checkpoint", and the
        wall it renders ("Continue", "Use another profile", "Create new
        account") matches none of the login patterns either. So a logged-out
        session was reported healthy indefinitely: the pool kept handing it
        out, every sweep using it returned nothing, and the drift canary
        then blamed the platform for changing its payload shape. Absence of
        a known failure string is not evidence of success; still being on
        the authenticated page is.

        `deny_paths` is the same confirmation for a probe whose AUTHENTICATED
        destination is not a fixed path, so `expect_path` cannot name it.
        Facebook's /me is the case: logged in it redirects to the account's
        own profile (`/<vanity>` or `/profile.php`), which differs per
        account, but logged out it lands on exactly one of a small, fixed
        set of doors, `/` or `/index.php`, and landing on one of THOSE
        is proof the authenticated page was not reachable. Matched on the
        settled path exactly, never as a prefix, so it cannot swallow a real
        profile path.
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
            if expect_path or deny_paths:
                from urllib.parse import urlparse

                landed = urlparse(page.url).path.rstrip("/")
                if expect_path and expect_path.rstrip("/") not in landed:
                    log.error(
                        f"session INVALID -- redirected off {expect_path!r} to {page.url} "
                        "(authenticated page not reachable, so these cookies are not logged in)"
                    )
                    return False
                # "" is the settled path of the site root once rstripped
                if any(landed == p.rstrip("/") for p in deny_paths):
                    log.error(
                        f"session INVALID -- {probe_url} landed on the logged-out "
                        f"door {page.url} (these cookies are not logged in)"
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
