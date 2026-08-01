"""LinkedIn discovery engine: search, crawling, and URL/identity extraction
-- keywords in, candidate profile URLs out.

Also owns the browser session (login/checkpoint detection) and URL/identity
normalization: both are produced here first and re-used by
analysis_engine.py, which imports them rather than redefining them, so there
is exactly one definition of each across the two files.

STATUS: PLUMBING ONLY -- NOT PRODUCTION READY. See analysis_engine.py's
module docstring for why: this project's own rule is network interception
first, verified against a real session, before any field- or result-parsing
code is written -- and no LinkedIn session is available in this environment
to do that against LinkedIn's actual search response shape.

What's real here: navigating to LinkedIn's own people-search URL (a stable,
long-documented pattern) using the real session, and reporting whether the
sweep could even reach it. What's not: parsing any actual result out of the
response -- a sweep always reports zero hits with an explanatory note rather
than a guessed selector that might silently return the wrong profiles (or
none) once run for real.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlparse

from backend.stealth.browser import Session
from backend.platforms.facebook.discovery_engine import Hit
from backend.shared.text import normalized_host, parse_normalized_url

# ───────────────────────────── URL / identity ──────────────────────────────
#
# Unlike the field-reading in analysis_engine.py, this is plain string
# handling against a URL scheme that has been stable for years -- no live
# session needed to get this right, and it needed to exist before anything
# else here could be tested.

PUBLIC_PROFILE = re.compile(r"^/in/([^/]+)/?$")
COMPANY = re.compile(r"^/company/([^/]+)/?$")


def normalize_url(url: str) -> str:
    p = parse_normalized_url(url)
    if p is None:
        return ""
    host = normalized_host(p)
    if "linkedin.com" in host:
        host = "www.linkedin.com"
    path = unquote(p.path).rstrip("/")
    return f"https://{host}{path}"


def profile_id(url: str) -> str:
    """The public identifier out of /in/<id> or /company/<id> -- LinkedIn's
    own vanity slug, not a numeric id (it doesn't expose one to a viewer)."""
    path = urlparse(normalize_url(url)).path
    for pattern in (PUBLIC_PROFILE, COMPANY):
        if m := pattern.match(path):
            return m.group(1)
    seg = [s for s in path.split("/") if s]
    return seg[-1] if seg else ""


def entity_type(url: str) -> str:
    return "page" if "/company/" in normalize_url(url) else "profile"


# ─────────────────────────── session / login state ─────────────────────────
#
# UNVERIFIED -- unlike twitter/instagram/facebook's session handling, this has
# not been run against a real LinkedIn cookie set (none is available in this
# environment). The URL and the `/checkpoint/` path are well-documented and
# stable; the login/checkpoint wording below is a best-effort guess and needs
# confirming against a live session before this platform's check_session()
# can be trusted the way the other platforms already are.

FEED = "https://www.linkedin.com/feed/"

RE_LOGIN = re.compile(r"(Sign in|Welcome [Bb]ack|Join LinkedIn)", re.I)
RE_CHECKPOINT = re.compile(
    r"(quick security check|unusual activity|verify (it.s|that it.s) you|"
    r"help us protect your account)",
    re.I,
)


class LinkedInSession(Session):
    async def check_session(self) -> bool:  # type: ignore[override]
        return await super().check_session(FEED, RE_LOGIN, RE_CHECKPOINT)


# ───────────────────────────── crawling / pagination ───────────────────────

SEARCH_URL = "https://www.linkedin.com/search/results/people/?keywords={q}"

NOT_YET_IMPLEMENTED = (
    "LinkedIn result parsing not yet implemented -- needs verification "
    "against a live session (see this module's docstring)"
)


@dataclass
class Sweep:
    keyword: str
    tab: str = "people"
    hits: list[Hit] = field(default_factory=list)
    pages: int = 0
    stopped: str = ""
    complete: bool = False
    seconds: float = 0.0
    error: str = ""

    def summary(self) -> str:
        return f"{len(self.hits)} hits, {self.stopped}"


class Discovery:
    """Runs keyword sweeps on an already-started browser session."""

    def __init__(self, args, ctx):
        self.a = args
        self.ctx = ctx

    async def sweep(self, keyword: str, tab: str = "people") -> Sweep:
        out = Sweep(keyword=keyword, tab=tab, stopped="not-implemented")
        started = time.time()
        page = await self.ctx.new_page()
        try:
            url = SEARCH_URL.format(q=quote(keyword))
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=self.a.timeout * 1000
                )
            except Exception as e:
                out.error = f"navigation failed: {type(e).__name__}: {e}"
                return out
            if "/login" in page.url or "/authwall" in page.url:
                out.error = "session invalid or checkpointed"
                return out
            out.pages = 1
            out.error = NOT_YET_IMPLEMENTED
            return out
        finally:
            out.seconds = time.time() - started
            try:
                await page.close()
            except Exception:
                pass

    async def run(self, keywords: list[str], tabs=None) -> list[Sweep]:
        """Sequential on purpose: unverified code has no business racing
        multiple pages against a real session."""
        return [await self.sweep(k) for k in keywords]


def merge(sweeps: list[Sweep]) -> list[Hit]:
    seen: dict[str, Hit] = {}
    for s in sweeps:
        for h in s.hits:
            seen.setdefault(h.entity_id or h.url, h)
    return list(seen.values())
