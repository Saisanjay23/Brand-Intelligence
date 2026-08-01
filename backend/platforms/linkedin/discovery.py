"""LinkedIn discovery: keywords -> candidate profile URLs.

STATUS: PLUMBING ONLY -- NOT PRODUCTION READY. See analysis.py's module
docstring for why: this project's own rule is network interception first,
verified against a real session, before any field- or result-parsing code
is written -- and no LinkedIn session is available in this environment to
do that against LinkedIn's actual search response shape.

What's real here: navigating to LinkedIn's own people-search URL (a stable,
long-documented pattern) using the real session, and reporting whether the
sweep could even reach it. What's not: parsing any actual result out of the
response -- a sweep always reports zero hits with an explanatory note rather
than a guessed selector that might silently return the wrong profiles (or
none) once run for real.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import quote

from backend.platforms.facebook.discovery.parse import Hit

SEARCH_URL = "https://www.linkedin.com/search/results/people/?keywords={q}"

NOT_YET_IMPLEMENTED = (
    "LinkedIn result parsing not yet implemented -- needs verification "
    "against a live session (see discovery.py module docstring)"
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
