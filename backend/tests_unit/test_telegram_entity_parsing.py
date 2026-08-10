"""entity_from / _iso (backend/platforms/telegram/discovery_engine.py) --
the Telethon User/Channel/Chat -> TelegramEntity mapping. Zero test
coverage for the whole Telegram platform module before this file. Pure
getattr-based duck-typing over whatever object Telethon hands back, so a
SimpleNamespace stands in for a real Telethon object without needing the
library installed or a real connection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from backend.platforms.telegram.discovery_engine import _iso, entity_from


def _photo(empty: bool):
    # Telethon distinguishes "has a photo" from "has none" by the TYPE
    # name containing "Empty" (UserProfilePhotoEmpty vs UserProfilePhoto)
    # -- entity_from keys off type(photo).__name__, not any attribute on it
    name = "UserProfilePhotoEmpty" if empty else "UserProfilePhoto"
    return type(name, (), {})()


class TestIso:
    def test_datetime_converts_to_utc_date_string(self):
        dt = datetime(2026, 7, 16, 3, 0, 0, tzinfo=timezone.utc)
        assert _iso(dt) == "2026-07-16"

    def test_naive_datetime_does_not_raise(self):
        # .astimezone() on a naive datetime assumes local system time rather
        # than raising -- just confirming this input shape is handled
        # gracefully, not asserting a specific date (system-timezone
        # dependent)
        result = _iso(datetime(2026, 7, 16))
        assert isinstance(result, str)

    def test_non_datetime_returns_empty_string(self):
        assert _iso("2026-07-16") == ""
        assert _iso(None) == ""
        assert _iso(12345) == ""


class TestEntityFromGuards:
    def test_none_returns_none(self):
        assert entity_from(None) is None

    def test_no_username_and_no_name_returns_none(self):
        obj = SimpleNamespace(username="", first_name="", last_name="", title="")
        assert entity_from(obj) is None

    def test_username_alone_with_no_name_is_still_accepted(self):
        obj = SimpleNamespace(username="adanigroup", first_name="", last_name="", title="")
        e = entity_from(obj)
        assert e is not None
        assert e.username == "adanigroup"


class TestEntityFromUserAccount:
    def test_a_user_with_no_title_is_kind_profile(self):
        obj = SimpleNamespace(
            username="hari", first_name="Hari", last_name="Sundar", title="",
            id=123, photo=None,
        )
        e = entity_from(obj)
        assert e.kind == "profile"
        assert e.title == "Hari Sundar"
        assert e.entity_id == "123"

    def test_a_user_account_never_gets_a_created_iso_even_if_date_is_present(self):
        # users have no creation-date field in the real protocol at all,
        # but even if some duck-typed stand-in carried a `date`, kind ==
        # "profile" must still suppress it -- channels/groups only
        obj = SimpleNamespace(
            username="hari", first_name="Hari", last_name="", title="",
            id=123, photo=None, date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        e = entity_from(obj)
        assert e.created_iso == ""

    def test_first_and_last_name_combine_with_a_single_space(self):
        obj = SimpleNamespace(username="", first_name="Hari", last_name="Sundar", title="")
        assert entity_from(obj).title == "Hari Sundar"

    def test_only_first_name_has_no_trailing_space(self):
        obj = SimpleNamespace(username="", first_name="Hari", last_name="", title="")
        assert entity_from(obj).title == "Hari"


class TestEntityFromChannelVsGroup:
    def test_broadcast_true_is_a_channel(self):
        obj = SimpleNamespace(username="adaninews", title="Adani News", broadcast=True, id=1, photo=None)
        assert entity_from(obj).kind == "channel"

    def test_broadcast_false_is_a_group(self):
        obj = SimpleNamespace(username="adanichat", title="Adani Chat", broadcast=False, id=1, photo=None)
        assert entity_from(obj).kind == "group"

    def test_channel_or_group_gets_created_iso_from_date(self):
        obj = SimpleNamespace(
            username="adaninews", title="Adani News", broadcast=True, id=1, photo=None,
            date=datetime(2020, 3, 15, tzinfo=timezone.utc),
        )
        assert entity_from(obj).created_iso == "2020-03-15"


class TestPhotoDetection:
    def test_photo_none_means_no_photo(self):
        obj = SimpleNamespace(username="x", title="", first_name="X", last_name="", id=1, photo=None)
        e = entity_from(obj)
        assert e.has_photo is False
        assert e.avatar == ""

    def test_empty_photo_type_means_no_photo(self):
        obj = SimpleNamespace(username="x", title="", first_name="X", last_name="", id=1, photo=_photo(empty=True))
        e = entity_from(obj)
        assert e.has_photo is False
        assert e.avatar == ""

    def test_real_photo_type_means_has_photo_and_builds_an_avatar_url(self):
        obj = SimpleNamespace(username="adanigroup", title="", first_name="X", last_name="", id=1, photo=_photo(empty=False))
        e = entity_from(obj)
        assert e.has_photo is True
        assert e.avatar == "https://t.me/i/userpic/320/adanigroup.jpg"

    def test_has_photo_but_no_username_still_yields_no_avatar_url(self):
        # the avatar URL needs BOTH a username and a real photo
        obj = SimpleNamespace(username="", title="Group", broadcast=True, id=1, photo=_photo(empty=False))
        e = entity_from(obj)
        assert e.has_photo is True
        assert e.avatar == ""


class TestOtherFlags:
    def test_verified_scam_restricted_are_read_from_the_object(self):
        obj = SimpleNamespace(
            username="x", title="", first_name="X", last_name="", id=1, photo=None,
            verified=True, scam=True, restricted=True,
        )
        e = entity_from(obj)
        assert e.verified is True
        assert e.scam is True
        assert e.restricted is True

    def test_missing_flags_default_to_false(self):
        obj = SimpleNamespace(username="x", title="", first_name="X", last_name="", id=1, photo=None)
        e = entity_from(obj)
        assert e.verified is False
        assert e.scam is False
        assert e.restricted is False

    def test_members_count_comes_from_participants_count(self):
        obj = SimpleNamespace(
            username="adanichat", title="Adani Chat", broadcast=False, id=1, photo=None,
            participants_count=5000,
        )
        assert entity_from(obj).members == 5000

    def test_entity_id_missing_defaults_to_empty_string(self):
        obj = SimpleNamespace(username="x", title="", first_name="X", last_name="", photo=None)
        assert entity_from(obj).entity_id == ""
