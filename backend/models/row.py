"""The scan record: one suspect profile, its raw scraped fields, and its
derived score -- what a platform's analysis adapter builds up field-by-field
over the course of visiting one profile. Every `platforms/*/analysis/*.py`
adapter constructs and mutates one of these directly (`row.mark(...)`,
`row.note(...)`, `row.has_custom_pic = True`, ...); this exact shape is
part of that contract and must not change without updating every adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.models.scoring import (ACTIVE_WINDOW_DAYS, BASE, NAME_THRESHOLD,
                                        REACH_AT, W_ACTIVE, W_LOGO, W_NAME, W_REACH)


@dataclass
class Row:
    url: str
    target: str
    original_feed: str = ""

    status: str = "PENDING"
    entity_type: str = "profile"
    profile_id: str = ""
    profile_name: str = ""
    created_iso: str = ""
    followers: Optional[int] = None
    followers_exact: str = ""
    friends: Optional[int] = None
    location: str = ""
    last_post_iso: str = ""
    posts_seen: str = ""  # yes | no | ""
    profile_pic_url: str = ""
    has_custom_pic: Optional[bool] = None
    screenshot: str = ""
    notes: str = ""
    name_score: int = 0
    src: dict[str, str] = field(default_factory=dict)  # field -> where it came from

    def note(self, m: str) -> None:
        if m not in self.notes:
            self.notes = f"{self.notes}; {m}".strip("; ")

    def mark(self, fld: str, source: str) -> None:
        self.src[fld] = source

    # -- derived --

    @property
    def logo_yes(self) -> str:
        if self.has_custom_pic is None:
            return ""
        return "Yes" if self.has_custom_pic else "No"

    @property
    def active_yes(self) -> str:
        if not self.last_post_iso:
            return "No" if self.posts_seen == "no" else ""
        try:
            dt = datetime.strptime(self.last_post_iso[:10], "%Y-%m-%d")
        except ValueError:
            return ""
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=ACTIVE_WINDOW_DAYS)
        return "Yes" if dt >= cutoff else "No"

    @property
    def name_yes(self) -> str:
        if not self.profile_name:
            return ""
        return "Yes" if self.name_score >= NAME_THRESHOLD else "No"

    @property
    def risk(self) -> int:
        """BASE..MAX_SCORE. A blank field scores nothing, never a penalty --
        "not visible to this session" is not evidence of innocence."""
        s = BASE
        if self.logo_yes == "Yes":
            s += W_LOGO
        if self.name_yes == "Yes":
            s += W_NAME
        if self.active_yes == "Yes":
            s += W_ACTIVE
        if (self.followers or 0) >= REACH_AT:
            s += W_REACH
        return s

    @property
    def priority(self) -> str:
        """Driven by the profile photo, not by the total -- lifting the
        brand's own photo is the act that makes an impersonation convincing,
        so it sets High on its own."""
        if self.logo_yes == "Yes":
            return "High"
        return "Medium" if self.name_yes == "Yes" else "Low"
