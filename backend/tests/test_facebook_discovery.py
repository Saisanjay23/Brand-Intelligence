"""Regression coverage for the Facebook discovery bug where some cards
showed a bare numeric id with no photo (discovery_source "id-backfill"/
"processed-not-shown" -- ids Facebook's search matched but never rendered
as a full edge, so name/avatar were never even attempted). The fix
(_extract_entity, used by Discovery._resolve_missing) reads a profile
page's own embedded payloads for that id's name/photo -- these tests pin
its scoping so a future change can't quietly start attaching the WRONG
profile's name/photo to a candidate, which would surface as a false-
positive impersonation match (a bogus high name-score badge on a profile
that was never actually a match) rather than the honest "no name
available" a scoping miss should produce.
"""

from __future__ import annotations

from backend.platforms.facebook.discovery_engine import GENERIC_NAMES, _extract_entity


def test_extracts_name_and_avatar_for_the_matching_id():
    blobs = [
        {"id": "12345", "name": "Jane Doe", "profile_picture": {"uri": "https://scontent.fbcdn.net/pic.jpg"}},
    ]
    name, avatar, has_custom = _extract_entity(blobs, "12345")
    assert name == "Jane Doe"
    assert avatar == "https://scontent.fbcdn.net/pic.jpg"
    assert has_custom is True


def test_ignores_other_entities_on_the_same_page():
    """The page for id 12345 also mentions a suggested friend (99999) --
    that name/photo must never leak onto the 12345 candidate. This is the
    exact false-positive-impersonation risk: a wrong name here could
    coincidentally read as a closer keyword match than the profile
    actually is."""
    blobs = [
        {"id": "99999", "name": "Someone Else", "profile_picture": {"uri": "https://scontent.fbcdn.net/other.jpg"}},
        {"id": "12345", "name": "Jane Doe", "profile_picture": {"uri": "https://scontent.fbcdn.net/pic.jpg"}},
    ]
    name, avatar, _ = _extract_entity(blobs, "12345")
    assert name == "Jane Doe"
    assert avatar == "https://scontent.fbcdn.net/pic.jpg"


def test_blank_when_no_dict_matches_the_id():
    """Blank beats wrong: an id this can't verify must never fall back to
    guessing from whatever's on the page."""
    blobs = [{"id": "99999", "name": "Someone Else"}]
    name, avatar, has_custom = _extract_entity(blobs, "12345")
    assert name == ""
    assert avatar == ""
    assert has_custom is False


def test_placeholder_avatar_never_counts_as_a_custom_photo():
    blobs = [{"id": "12345", "name": "Jane Doe", "profile_picture": {"uri": "https://static.xx.fbcdn.net/rsrc.php/silhouette.png"}}]
    name, avatar, has_custom = _extract_entity(blobs, "12345")
    assert name == "Jane Doe"
    assert avatar == ""
    assert has_custom is False


def test_generic_chrome_name_is_rejected_not_treated_as_the_profile_name():
    """A login wall / checkpoint page's own boilerplate ("Facebook",
    "Notifications") must never be mistaken for a profile's real name --
    _resolve_missing also has its own RE_LOGIN/RE_CHECKPOINT/RE_GONE guard
    before ever calling this, but the name-level check here is a second,
    independent line of defense against the same false-positive risk."""
    for generic in GENERIC_NAMES:
        blobs = [{"id": "12345", "name": generic.title()}]
        name, _, _ = _extract_entity(blobs, "12345")
        assert name == "", f"{generic!r} should have been rejected as a real name"


def test_first_matching_dict_wins_when_several_mention_the_same_id():
    """Multiple payloads on the page can independently mention the same
    entity (e.g. embedded state + an XHR echo) -- the first non-blank
    name/photo found is kept, later ones don't overwrite it with something
    worse."""
    blobs = [
        {"id": "12345", "name": "Jane Doe", "profile_picture": {"uri": "https://scontent.fbcdn.net/pic.jpg"}},
        {"id": "12345", "name": "Stale Cached Name"},
    ]
    name, avatar, _ = _extract_entity(blobs, "12345")
    assert name == "Jane Doe"
    assert avatar == "https://scontent.fbcdn.net/pic.jpg"
