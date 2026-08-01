"""Per-platform health, measured from what actually happened.

A platform is not "up" or "down" -- it degrades. Sessions get challenged,
payload shapes drift, a scanner starts returning PARTIAL rows. This records
the outcome of every profile touched and turns the recent history into one
number, so a scanner that has quietly stopped working is visible before
someone notices the results are empty.

Kept in memory: this is an operational signal, not an audit record (that's
`services/incident_service.py`'s job).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

WINDOW = 200  # outcomes remembered per platform

BAD_STATUSES = {"ERROR", "LOGIN_REQUIRED", "CHECKPOINT"}
PARTIAL_STATUSES = {"PARTIAL", "GONE"}


@dataclass
class _PlatformHealth:
    platform: str
    ok: int = 0
    partial: int = 0
    bad: int = 0
    last_error: str = ""
    last_seen: float = 0.0
    recent: deque = field(default_factory=lambda: deque(maxlen=WINDOW))

    @property
    def total(self) -> int:
        return self.ok + self.partial + self.bad

    @property
    def score(self) -> float:
        """1.0 healthy, 0 dead. Partials count half -- they are a warning."""
        if not self.recent:
            return 1.0
        weight = {"ok": 1.0, "partial": 0.5, "bad": 0.0}
        return sum(weight[r] for r in self.recent) / len(self.recent)

    @property
    def state(self) -> str:
        s = self.score
        if not self.recent:
            return "unknown"
        if s >= 0.7:
            return "healthy"
        return "degraded" if s >= 0.4 else "critical"

    def to_dict(self) -> dict:
        return {
            "platform": self.platform, "state": self.state, "score": round(self.score, 3),
            "ok": self.ok, "partial": self.partial, "bad": self.bad, "total": self.total,
            "last_error": self.last_error, "last_seen": int(self.last_seen) if self.last_seen else 0,
        }


_by_platform: dict[str, _PlatformHealth] = {}


def _get(platform: str) -> _PlatformHealth:
    if platform not in _by_platform:
        _by_platform[platform] = _PlatformHealth(platform)
    return _by_platform[platform]


def record(platform: str, status: str, note: str = "") -> None:
    h = _get(platform)
    h.last_seen = time.time()
    if status in BAD_STATUSES:
        h.bad += 1
        h.recent.append("bad")
        h.last_error = note or status
    elif status in PARTIAL_STATUSES:
        h.partial += 1
        h.recent.append("partial")
    else:
        h.ok += 1
        h.recent.append("ok")


def all_platforms() -> list[dict]:
    return [h.to_dict() for h in _by_platform.values()]


def one(platform: str) -> dict:
    return _get(platform).to_dict()
