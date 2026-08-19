"""Pacing that looks like a person reading, not a script iterating.

The single biggest detection signal is rhythm: identical gaps between
identical actions. Everything here exists to break that up.

Deliberately small. Mouse-jitter and typing simulation are omitted because
this tool never types or clicks, it navigates and reads payloads, so faking
input events would add signal, not remove it.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime

# rough seconds per action, before jitter and the multipliers below
BASE = {
    "page_load": 0.8,
    "between_profiles": 2.2,
    "between_pages": 0.5,
    "tab_switch": 1.0,
    "default": 1.0,
}


class Human:
    """Per-session pacing. Fatigue and time-of-day shape the delays."""

    def __init__(self, session_start: datetime | None = None):
        self.started = session_start or datetime.now()
        self.actions = 0

    def fatigue(self) -> float:
        """People slow down. After ~200 actions this is a 1.5x drag."""
        return min(1.0 + self.actions / 400, 1.5)

    def circadian(self) -> float:
        """Nobody browses at 4am the way they do at lunchtime."""
        hour = datetime.now().hour
        if 1 <= hour < 6:
            return 1.6
        if 6 <= hour < 9 or 22 <= hour <= 23:
            return 1.2
        return 1.0

    def delay_for(self, context: str = "default", scale: float = 1.0) -> float:
        base = BASE.get(context, BASE["default"]) * scale
        # lognormal: mostly short gaps, occasionally a long one, never zero
        jitter = random.lognormvariate(0, 0.35)
        return max(0.15, base * jitter * self.fatigue() * self.circadian())

    async def pause(self, context: str = "default", scale: float = 1.0) -> None:
        self.actions += 1
        await asyncio.sleep(self.delay_for(context, scale))

    def should_rest(self) -> bool:
        """Occasional longer gap, the way a person gets distracted."""
        return self.actions > 0 and self.actions % random.randint(25, 45) == 0

    async def maybe_rest(self) -> float:
        if not self.should_rest():
            return 0.0
        nap = random.uniform(20, 60)
        await asyncio.sleep(nap)
        return nap

    async def warmup_delay(self, scale: float = 1.0) -> float:
        """Initial orientation delay after session launch before commencing sweeps."""
        nap = random.uniform(1.2, 3.5) * scale
        await asyncio.sleep(nap)
        return nap
