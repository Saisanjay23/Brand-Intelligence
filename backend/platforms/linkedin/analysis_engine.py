"""LinkedIn analysis engine: validation -- profile URL -> scored Row.

Session/login-checking and URL/identity normalization live in
discovery_engine.py (imported below) since discovery produces them first;
this file owns the browser-drive loop (Scraper).

STATUS: PLUMBING ONLY -- NOT PRODUCTION READY.

Session handling, navigation, pacing, and error isolation here are real and
match every other platform's Scraper exactly (same start/stop/pause/
check_session/one interface registry.py expects), so this wires in cleanly
and fails safely.

What is deliberately NOT here: field-reading. Every other platform's fill()
was written after first intercepting a real logged-in session's network
traffic (see the module docstrings in twitter/discovery_engine.py,
facebook/analysis_engine.py) -- this project's own rule, enforced all the way
through, is "network interception first, verify empirically before writing
code". No LinkedIn session is available in this environment to do that, and
guessing at LinkedIn's Voyager API shape (or its DOM structure) and shipping
it unverified is exactly the kind of thing that gets an account flagged on
the platform that is by far the most aggressive of the five about detecting
scraping.

So `one()` below visits the real profile, confirms the session is still
alive, and returns a Row honestly marked PARTIAL with a note explaining
why -- instead of fabricating a name/follower count/logo verdict that looks
plausible and might be wrong. Wiring up real field extraction is a follow-up
once a real LinkedIn session (cookies) is available: export them the same
way as any other platform (Sessions -> Launch Interactive Browser Login, or
paste a cookie export), then this needs the same live-probe-first pass every
other platform already got.
"""

from __future__ import annotations

from pathlib import Path

from backend.models.row import Row
from backend.platforms.linkedin.discovery_engine import (LinkedInSession,
                                                          entity_type,
                                                          normalize_url,
                                                          profile_id)


class Scraper:
    """Same surface as every other platform's Scraper; see module docstring
    for what is and isn't implemented yet."""

    normalize_url = staticmethod(normalize_url)

    def __init__(
        self,
        args,
        cookies: list[dict],
        session_id: str = "",
        proxy: dict | None = None,
    ):
        self.a = args
        self.evidence = Path(args.evidence) if args.evidence else None
        if self.evidence:
            self.evidence.mkdir(parents=True, exist_ok=True)
        self.session = LinkedInSession(
            args,
            cookies,
            load_images=bool(self.evidence),
            session_id=session_id,
            proxy=proxy,
        )

    @property
    def ctx(self):
        return self.session.ctx

    async def start(self):
        await self.session.start()

    async def stop(self):
        await self.session.stop()

    async def pause(self, mult: float = 1.0):
        await self.session.pause(mult)

    async def check_session(self) -> bool:
        return await self.session.check_session()

    async def process(self, raw_url: str, target: str, feed: str) -> Row:
        url = normalize_url(raw_url)
        row = Row(url=url, target=target, original_feed=feed)
        row.profile_id = profile_id(url)
        row.entity_type = entity_type(url)

        page = await self.ctx.new_page()
        try:
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=self.a.timeout * 1000
                )
            except Exception:
                row.status = "ERROR"
                row.note("navigation failed")
                return row

            if "/login" in page.url or "/authwall" in page.url:
                row.status = "LOGIN_REQUIRED"
                row.note("cookies rejected/expired")
                return row

            # The navigation itself is real and proves the URL resolves and
            # the session isn't logged out on this specific page -- but name/
            # followers/location/last-post/logo reading is the part that
            # needs a live payload to write correctly, not a guess.
            row.status = "PARTIAL"
            row.note(
                "LinkedIn field extraction not yet implemented -- needs "
                "verification against a live session (see this module's "
                "docstring) before this platform's data can be trusted"
            )
            return row
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def one(self, u: str, tgt: str, feed: str) -> Row:
        """process() with a failed profile turned into a reportable row --
        same per-URL isolation as every other platform, so one bad profile
        never takes a batch down."""
        try:
            return await self.process(u, tgt, feed)
        except Exception as e:
            row = Row(url=normalize_url(u), target=tgt, original_feed=feed)
            row.profile_id = profile_id(row.url)
            row.status = "ERROR"
            row.note(f"{type(e).__name__}: {e}")
            return row
