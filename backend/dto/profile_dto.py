"""Request shape for PATCH /profiles/{profile_id}."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ProfilePatch(BaseModel):
    status: Optional[str] = None
    has_logo: Optional[bool] = None
    logo_match: Optional[bool] = None
    username_match: Optional[bool] = None
    is_active: Optional[bool] = None
    has_name_match: Optional[bool] = None
    risk_score: Optional[int] = None
    priority: Optional[str] = None
    comments: Optional[str] = None
    target: Optional[str] = None
    official_feed: Optional[str] = None
    display_name: Optional[str] = None
    followers: Optional[int] = None
    location: Optional[str] = None
    last_post_date: Optional[str] = None
    # analyst hand-edits to the published-incident preview -- flat dotted
    # keys, e.g. {"title": "...", "socialProfileInfo.location": "..."};
    # see profile_repository.patch()'s dotted-path expansion.
    incident_overrides: Optional[dict[str, Any]] = None


class PublishAllRequest(BaseModel):
    client_id: str
    platform: Optional[str] = None
