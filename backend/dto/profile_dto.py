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
    # analyst hand-edits to the published-incident preview, flat dotted
    # keys, e.g. {"title": "...", "socialProfileInfo.location": "..."};
    # see profile_repository.patch()'s dotted-path expansion.
    incident_overrides: Optional[dict[str, Any]] = None


class PublishAllRequest(BaseModel):
    client_id: str
    platform: Optional[str] = None
    # "all" (default) | "recent" (analysed in the last 24h) | "2days" | "week"
    #, the server computes the concrete cutoff itself (see
    # profile_service.publish_all_profiles), never trusts a client-supplied
    # timestamp.
    mode: str = "all"


class BulkPatchRequest(BaseModel):
    """Applies the same status (+ optional match flags) to many profiles at
    once, the bulk counterpart to PATCH /profiles/{id}, for triage actions
    like "approve everything selected" that would otherwise be one request
    per card."""
    profile_ids: list[str]
    status: str
    logo_match: Optional[bool] = None
    username_match: Optional[bool] = None


class BulkDeleteRequest(BaseModel):
    """Hard-deletes the named profile records, irreversible, unlike a
    status patch (reject/archive). See profile_service.delete_profiles."""
    profile_ids: list[str]


class DeletePlatformDataRequest(BaseModel):
    """Hard-deletes every profile (both Discovery and Analysis phase),
    evidence screenshot, and published incident for one client + platform.
    Irreversible. See profile_service.delete_for_client_platform."""
    client_id: str
    platform: str


class ManualUrlsRequest(BaseModel):
    """One or more profile URLs an analyst already has in hand, see
    profile_service.add_manual_urls. Platform is auto-detected per URL from
    its host; a URL whose host doesn't match a supported platform is
    reported back in the response's `skipped` list rather than erroring
    the whole batch.

    Three lists: `urls` is the untyped legacy path (best-effort keyword
    match across everything the client has configured); `individual_urls`/
    `domain_urls` is an analyst saying up front which bucket a URL belongs
    in (the UI's separate Executive/Domain boxes), so incident_publisher's
    person-vs-brand classification doesn't depend on the URL text happening
    to fuzzy-match a configured keyword. All three may be used together in
    one request."""
    client_id: str
    urls: list[str] = []
    individual_urls: list[str] = []
    domain_urls: list[str] = []


class ExportXlsxRequest(BaseModel):
    """The frontend already fetched, filtered, and shaped these rows (same
    logic that drives the CSV/JSON export), this endpoint only turns them
    into a real .xlsx binary via openpyxl, which the browser can't do itself
    without pulling in a vulnerable client-side library (see download.ts's
    rowsToExcelHtml docstring)."""
    filename: str
    rows: list[dict[str, Any]]
