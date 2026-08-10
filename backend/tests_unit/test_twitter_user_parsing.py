"""_user_from_result (backend/platforms/twitter/discovery_engine.py) -- the
legacy-vs-core field migration fallback the function's own docstring
flags as fragile: X has been moving fields out of `legacy` one at a time,
and a real 2026 capture had NO `legacy` key at all. Zero test coverage
despite that explicit warning. Every field must be checked against BOTH
shapes: a full legacy-only payload (older captures) and a fully-migrated
core/relationship_counts/tweet_counts/profile_bio/privacy shape (newer
captures), independently.
"""

from __future__ import annotations

from backend.platforms.twitter.discovery_engine import _user_from_result


def _legacy_result(**overrides) -> dict:
    base = {
        "rest_id": "12345",
        "legacy": {
            "screen_name": "cyfirma",
            "name": "CYFIRMA",
            "created_at": "Wed Jun 01 12:00:00 +0000 2016",
            "location": "Singapore",
            "followers_count": 5000,
            "friends_count": 200,
            "statuses_count": 900,
            "description": "Threat intel",
            "profile_image_url_https": "https://pbs.twimg.com/x_normal.jpg",
            "verified": False,
            "protected": False,
        },
    }
    base.update(overrides)
    return base


def _fully_migrated_result(**overrides) -> dict:
    """The shape confirmed live with NO `legacy` key at all."""
    base = {
        "rest_id": "12345",
        "core": {
            "screen_name": "cyfirma",
            "name": "CYFIRMA",
            "created_at": "Wed Jun 01 12:00:00 +0000 2016",
        },
        "location": {"location": "Singapore"},
        "relationship_counts": {"followers": 5000, "following": 200},
        "tweet_counts": {"tweets": 900},
        "profile_bio": {"description": "Threat intel"},
        "privacy": {"protected": False},
        "verification": {"verified": False},
        "avatar": {"image_url": "https://pbs.twimg.com/x_normal.jpg"},
    }
    base.update(overrides)
    return base


class TestGuards:
    def test_non_dict_input_returns_none(self):
        assert _user_from_result("not a dict") is None
        assert _user_from_result(None) is None
        assert _user_from_result([1, 2, 3]) is None

    def test_neither_legacy_nor_core_present_returns_none(self):
        assert _user_from_result({"rest_id": "1"}) is None

    def test_no_handle_and_no_rest_id_returns_none(self):
        assert _user_from_result({"legacy": {"name": "X"}}) is None

    def test_rest_id_alone_with_no_handle_is_still_accepted(self):
        # entity_id-only identity is still usable for dedup
        u = _user_from_result({"rest_id": "999", "legacy": {"name": "X"}})
        assert u is not None
        assert u.entity_id == "999"
        assert u.handle == ""


class TestLegacyShapeStillWorks:
    """Every field must still parse correctly from an OLDER capture that
    still has the full legacy object -- the fallback must never break the
    shape that already worked."""

    def test_every_field_reads_from_legacy(self):
        u = _user_from_result(_legacy_result())
        assert u.entity_id == "12345"
        assert u.handle == "cyfirma"
        assert u.name == "CYFIRMA"
        assert u.created_iso == "2016-06-01"
        assert u.location == "Singapore"
        assert u.followers == 5000
        assert u.following == 200
        assert u.posts == 900
        assert u.description == "Threat intel"
        assert u.verified is False
        assert u.protected is False

    def test_avatar_url_is_upgraded_from_the_48px_thumbnail(self):
        u = _user_from_result(_legacy_result())
        assert u.avatar == "https://pbs.twimg.com/x_400x400.jpg"

    def test_verified_true_via_legacy_flag(self):
        u = _user_from_result(_legacy_result(legacy={
            **_legacy_result()["legacy"], "verified": True,
        }))
        assert u.verified is True

    def test_is_blue_verified_at_the_top_level_also_counts(self):
        r = _legacy_result()
        r["is_blue_verified"] = True
        u = _user_from_result(r)
        assert u.verified is True


class TestFullyMigratedShapeWithNoLegacyKeyAtAll:
    """The live-confirmed 2026 capture shape: `legacy` is entirely absent."""

    def test_every_field_falls_back_to_its_new_sibling_object(self):
        u = _user_from_result(_fully_migrated_result())
        assert u.entity_id == "12345"
        assert u.handle == "cyfirma"
        assert u.name == "CYFIRMA"
        assert u.created_iso == "2016-06-01"
        assert u.location == "Singapore"
        assert u.followers == 5000
        assert u.following == 200
        assert u.posts == 900
        assert u.description == "Threat intel"
        assert u.verified is False
        assert u.protected is False

    def test_verification_object_verified_flag_is_read(self):
        r = _fully_migrated_result(verification={"verified": True})
        u = _user_from_result(r)
        assert u.verified is True

    def test_privacy_object_protected_flag_is_read(self):
        r = _fully_migrated_result(privacy={"protected": True})
        u = _user_from_result(r)
        assert u.protected is True

    def test_zero_followers_is_a_real_reading_not_treated_as_missing(self):
        # relationship_counts.followers = 0 must come through as 0, not
        # be skipped in favour of some other fallback -- `is None` is the
        # correct guard in the source, not a truthiness check
        r = _fully_migrated_result(relationship_counts={"followers": 0, "following": 0})
        u = _user_from_result(r)
        assert u.followers == 0
        assert u.following == 0


class TestMixedShapePartialMigration:
    """A real transitional payload could plausibly have SOME fields still
    in legacy and others already moved -- each field's own fallback must
    be independent, not all-or-nothing on whether `legacy` exists."""

    def test_legacy_present_but_missing_one_field_falls_back_for_that_field_only(self):
        r = {
            "rest_id": "12345",
            "legacy": {
                "screen_name": "cyfirma", "name": "CYFIRMA",
                # followers_count deliberately absent from legacy
            },
            "relationship_counts": {"followers": 7000},
        }
        u = _user_from_result(r)
        assert u.handle == "cyfirma"  # from legacy
        assert u.followers == 7000  # fell back to relationship_counts

    def test_created_at_prefers_legacy_over_core_when_both_present(self):
        r = _legacy_result(core={"created_at": "Wed Jun 01 12:00:00 +0000 2099"})
        u = _user_from_result(r)
        assert u.created_iso == "2016-06-01"  # legacy's value wins


class TestParseCreated:
    def test_malformed_date_string_yields_empty_created_iso(self):
        u = _user_from_result(_legacy_result(legacy={
            **_legacy_result()["legacy"], "created_at": "not a real date",
        }))
        assert u.created_iso == ""

    def test_missing_created_at_yields_empty_created_iso(self):
        legacy = dict(_legacy_result()["legacy"])
        legacy.pop("created_at")
        u = _user_from_result(_legacy_result(legacy=legacy))
        assert u.created_iso == ""
