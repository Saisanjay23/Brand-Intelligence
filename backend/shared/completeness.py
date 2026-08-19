"""What "fully scraped" means for one analysed profile, in one place.

THE GAP THIS CLOSES
    `profile_repository.RETRYABLE_ANALYSIS_STATUSES` already re-queues a
    row whose analysis STATUS says the profile was never actually looked
    at (ERROR/CHECKPOINT/LOGIN_REQUIRED/PARTIAL). It has no opinion at all
    about a row that came back status OK carrying half its fields, and
    that is the shape almost every real loss takes: the page loaded, the
    name and follower count came through, and the last-post date (or the
    evidence screenshot) simply was not on screen yet when it was read.

    Measured on one 663-URL run before this existed: 76 of 148 Twitter
    rows had no last-post date, 82 of 211 Instagram rows had none, and 28
    Instagram rows had no screenshot -- every one of them status OK or
    PARTIAL, none of them ever retried, because nothing in the pipeline
    could tell "this profile genuinely has no posts" apart from "we did
    not manage to read them this time".

WHY IT IS SAFE TO RETRY ON THIS
    `profile_repository.save()` writes only non-blank values
    (`v not in (None, "", {})`), so attempts MERGE: a second visit can
    fill a field the first one missed and can never blank one the first
    one got. Re-attempting an incomplete row is therefore monotonic -- it
    either improves the row or leaves it exactly as it was.

WHY EVERY RULE BELOW IS CONSERVATIVE
    A field is only required when the platform genuinely publishes it for
    that KIND of profile. "Missing" has to mean "we failed to read it",
    never "this account legitimately has none", or a private account with
    no visible posts would burn its whole retry budget on every sweep
    forever and then surface as a false coverage gap. Hence the explicit
    carve-outs for confirmed-zero-post accounts, private/protected
    profiles, groups, and profiles that are already gone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend.shared.models.row import Row

# Statuses where re-reading cannot help: the profile is gone, so there is
# nothing further to scrape and its blank fields are the honest answer.
TERMINAL_STATUSES = ("GONE",)

# A note that explains a legitimately unreadable timeline. Both are set by
# the engines themselves (twitter: "protected account -- posts not
# visible"; instagram: private accounts), and both mean the posts are
# hidden from this session by the platform, not missed by the scraper.
_HIDDEN_TIMELINE_MARKERS = ("protected", "private")


def _blank(value) -> bool:
    """Numeric 0 is a real reading (a brand-new account really can have 0
    followers); only None counts as never-read. Mirrors the same
    distinction `analysis_service._field_blank` already draws."""
    if value is None:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return False
    return not str(value).strip()


def _timeline_hidden(row: "Row") -> bool:
    notes = (row.notes or "").lower()
    return any(m in notes for m in _HIDDEN_TIMELINE_MARKERS)


def missing_fields(platform_id: str, row: "Row", *, want_screenshot: bool) -> list[str]:
    """The fields this row should have carried and did not.

    Empty list means "nothing further to gain from another visit" -- which
    includes a genuinely empty profile, not only a perfectly full one.
    `want_screenshot` is the run's evidence setting (see
    `analysis_service._evidence_dir`): with capture switched off, a missing
    screenshot is configuration, not a gap.
    """
    if row.status in TERMINAL_STATUSES:
        return []

    missing: list[str] = []

    if _blank(row.profile_name):
        missing.append("display name")

    # Facebook groups publish a member count under neither `followers` nor
    # `friends`; every other entity publishes one of the two (a Page its
    # followers, a personal profile its friends -- see
    # facebook/analysis_engine.py::followers_from_friends).
    if row.entity_type != "group" and _blank(row.followers) and _blank(row.friends):
        missing.append("followers")

    # "no posts at all" (posts_seen == "no") is a real, extracted answer,
    # and a hidden timeline is the platform's decision, not a miss.
    if (
        _blank(row.last_post_iso)
        and row.posts_seen != "no"
        and not _timeline_hidden(row)
    ):
        missing.append("last post date")

    if want_screenshot and _blank(row.screenshot):
        missing.append("screenshot")

    return missing


def is_complete(platform_id: str, row: "Row", *, want_screenshot: bool) -> bool:
    return not missing_fields(platform_id, row, want_screenshot=want_screenshot)
