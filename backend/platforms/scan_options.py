"""How a scan/sweep is run -- the configuration every platform adapter's
constructor takes.

Lives beside `contracts.py` rather than inside `analysis` or `discovery`
because it is a parameter type for the ports themselves (`ScraperPort`,
`DiscovererPort`), used by every caller that constructs an adapter --
the analysis module's job runner, the sessions module's on-demand health
check, and the CLI alike. Ported unchanged from `backend/core/options.py`
and `backend/core/discovery_options.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ScanOptions:
    evidence: Optional[str] = None  # screenshot dir; disables asset blocking
    headful: bool = False
    timeout: int = 45  # per-navigation, seconds
    settle: float = 12  # cap on waiting for the profile payload
    delay: float = 6.0  # between profiles, jittered
    scrolls: int = 0  # newest post is in the first render
    about: bool = False  # two extra page loads, no join date
    concurrency: int = 1  # >1 is faster and more conspicuous
    keep_going: bool = False  # continue past a checkpoint


@dataclass
class DiscoveryOptions:
    """Separate from ScanOptions because the two phases tune against
    different limits: analysis is paced to protect the session on repeated
    profile visits, discovery is paced by how fast the platform hands over
    the next results page."""

    headful: bool = False
    timeout: int = 45
    settle: float = 12  # cap on waiting for the first results render
    page_wait: float = 6.0  # cap on waiting for one more results page
    patience: int = 3  # scrolls with no new ids before calling it stalled
    concurrency: int = 2  # keyword sweeps in flight at once
    progress_every: int = 5  # log a progress line every N result pages

    # Caps. People search is effectively unbounded on some platforms, so a
    # sweep needs a stop somewhere. Hitting any cap marks the sweep
    # INCOMPLETE rather than pretending the results ran out.
    max_results: int = 0  # 0 = no cap
    max_pages: int = 0  # 0 = no cap
    max_seconds: float = 300  # per sweep; 0 = no cap
