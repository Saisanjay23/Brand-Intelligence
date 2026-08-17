"""Input and output shapes for the standalone engine.

Plain dataclasses with `from_dict`/`to_dict` on both sides, so the exact
same request can arrive as a Python object, a JSON file, or an HTTP body
without this layer caring which. Nothing here imports Mongo, FastAPI or
the `Job` dataclass, that independence is the entire point of the
package (see `runner.py`'s module docstring).

The per-profile output dicts deliberately reuse the SAME field names the
Mongo-backed path already writes (`services/discovery_service.py::_hit_to_fields`
and `services/analysis_service.py::_row_to_fields`), so a record produced
here is interchangeable with one produced by the API path, that is what
lets `sinks.MongoSink` hand these straight to `profile_repository.save_many`
with no translation, and what lets anyone already reading the API's output
read this without learning a second vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from urllib.parse import urlparse

# Which search surfaces each platform actually understands. Mirrors
# `services/discovery_service.py::PLATFORM_TABS`, duplicated rather than
# imported because that module imports `profile_repository` at module level,
# which would drag Motor into a package whose whole purpose is running
# without it. Kept in sync by `tests_unit/`'s parity test.
PLATFORM_TABS: dict[str, list[str]] = {
    "facebook": ["people", "pages", "groups"],
    "twitter": ["people"],
    "instagram": ["people"],
    "youtube": ["channels"],
    "telegram": ["all"],
    "tiktok": ["people"],
}

# Host -> platform, so an analysis run can be handed a bare list of URLs
# with no platform column. The API path never needs this (a profile
# document already knows its own platform); a standalone caller pointing
# the engine at a hand-assembled URL list has nothing else to go on.
_HOSTS: dict[str, str] = {
    "facebook.com": "facebook", "fb.com": "facebook", "fb.watch": "facebook",
    "twitter.com": "twitter", "x.com": "twitter",
    "instagram.com": "instagram",
    "youtube.com": "youtube", "youtu.be": "youtube",
    "t.me": "telegram", "telegram.me": "telegram",
    "tiktok.com": "tiktok",
}


def platform_for_url(url: str) -> str:
    """Best-effort platform id for a profile URL, "" when unrecognised.

    Matches on the registrable suffix rather than the exact host so
    `m.facebook.com`, `www.instagram.com` and `mobile.twitter.com` all
    resolve without enumerating every subdomain any platform has ever used.
    """
    host = (urlparse(url if "//" in url else f"//{url}").hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    if host in _HOSTS:
        return _HOSTS[host]
    for known, platform_id in _HOSTS.items():
        if host.endswith("." + known):
            return platform_id
    return ""


@dataclass
class DiscoveryRequest:
    """Keywords in, candidate profiles out.

    `platforms` empty means "every platform that has a discovery phase AND
    usable credentials right now", the standalone equivalent of the API
    path's "All Platforms" sweep, resolved against `credentials.py` instead
    of against the Mongo session pool.
    """

    keywords: list[str]
    platforms: list[str] = field(default_factory=list)
    # The brand/person the keywords are hunting for, used for the name-match
    # score on each hit. Defaults to the first keyword, which is what a
    # single-keyword run almost always wants.
    target: str = ""
    tabs: dict[str, list[str]] = field(default_factory=dict)
    max_results: int = 0  # 0 = uncapped, per (keyword, tab)
    max_seconds: float = 300.0  # per sweep; 0 = no cap
    concurrency: int = 2  # keyword sweeps in flight within one platform
    platform_concurrency: int = 1  # platforms swept at once; 1 = one browser
    headful: bool = False
    client_id: str = "standalone"  # only ever used by the optional Mongo sink

    @classmethod
    def from_dict(cls, d: dict) -> "DiscoveryRequest":
        return cls(
            keywords=[str(k).strip() for k in (d.get("keywords") or []) if str(k).strip()],
            platforms=[str(p).strip().lower() for p in (d.get("platforms") or []) if str(p).strip()],
            target=str(d.get("target") or ""),
            tabs={str(k): list(v) for k, v in (d.get("tabs") or {}).items()},
            max_results=int(d.get("max_results") or 0),
            max_seconds=float(d.get("max_seconds") or 300.0),
            concurrency=int(d.get("concurrency") or 2),
            platform_concurrency=int(d.get("platform_concurrency") or 1),
            headful=bool(d.get("headful", False)),
            client_id=str(d.get("client_id") or "standalone"),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def resolved_target(self) -> str:
        return self.target or (self.keywords[0] if self.keywords else "")

    def tabs_for(self, platform_id: str) -> list[str]:
        return self.tabs.get(platform_id) or PLATFORM_TABS.get(platform_id) or ["people"]


@dataclass
class AnalysisRequest:
    """Profile URLs in, scored rows out.

    URLs carry their own platform (see `platform_for_url`), so one request
    may span several platforms; the runner groups them and opens one
    session per platform rather than one per URL.
    """

    urls: list[str]
    target: str = ""
    official_feed: str = ""
    # Force every URL onto one platform instead of inferring per-URL. For a
    # shortlink or a vanity domain that fronts a real profile, inference has
    # nothing to go on and this is the only way through.
    platform: str = ""
    evidence_dir: str = ""  # "" = fall back to settings; "-" = capture nothing
    delay: float = 0.0  # 0 = use settings.analysis_delay_sec
    concurrency: int = 1
    headful: bool = False
    client_id: str = "standalone"

    @classmethod
    def from_dict(cls, d: dict) -> "AnalysisRequest":
        return cls(
            urls=[str(u).strip() for u in (d.get("urls") or []) if str(u).strip()],
            target=str(d.get("target") or ""),
            official_feed=str(d.get("official_feed") or ""),
            platform=str(d.get("platform") or "").strip().lower(),
            evidence_dir=str(d.get("evidence_dir") or ""),
            delay=float(d.get("delay") or 0.0),
            concurrency=int(d.get("concurrency") or 1),
            headful=bool(d.get("headful", False)),
            client_id=str(d.get("client_id") or "standalone"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PlatformOutcome:
    """What happened on one platform, kept separate from the profiles so a
    caller can tell "found nothing" apart from "never ran".

    One platform failing must never be reported as the whole run failing,
    the API path makes the same distinction (`discovery_service`'s per-
    platform `platform_status`), and it matters more here, because a
    standalone caller has no job-events stream to inspect afterwards.
    """

    platform: str
    status: str  # done | partial | failed | skipped
    reason: str = ""  # why it was skipped, or how it broke
    found: int = 0  # profiles this platform contributed
    attempted: int = 0  # sweep units, or URLs, actually processed
    total: int = 0  # sweep units, or URLs, asked for
    session: str = ""  # which credential was used, for reproducing a run
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EngineResult:
    """The whole run: every profile, plus per-platform outcomes.

    `ok` is False only when NOTHING ran, no credentials at all, or every
    platform failed. A run where two platforms succeeded and one broke is a
    successful run with a failed outcome recorded in `platforms`, and the
    exit code / return value must reflect that rather than throwing away
    the results that did come back.
    """

    kind: str  # discovery | analysis
    profiles: list[dict] = field(default_factory=list)
    platforms: list[PlatformOutcome] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return any(p.status in ("done", "partial") for p in self.platforms)

    @property
    def found(self) -> int:
        return len(self.profiles)

    def outcome(self, platform_id: str) -> Optional[PlatformOutcome]:
        return next((p for p in self.platforms if p.platform == platform_id), None)

    def summary(self) -> str:
        parts = [f"{p.platform}: {p.status}" + (f" ({p.reason})" if p.reason else "") for p in self.platforms]
        return f"{self.kind}: {self.found} profile(s) in {self.seconds:.1f}s -- " + "; ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ok": self.ok,
            "found": self.found,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "seconds": round(self.seconds, 2),
            "platforms": [p.to_dict() for p in self.platforms],
            "errors": list(self.errors),
            "profiles": list(self.profiles),
        }
