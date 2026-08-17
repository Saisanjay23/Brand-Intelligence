"""URL normalization + identity extraction (normalize_url / profile_id /
handle_of / username_of / channel_ref), one platform per test class.

This is the layer every platform's dedup (profile_repository.py::save,
matched by entity_id first, url second) and discovery/analysis join depend
on -- two URLs that are really the same profile must normalize to the same
identity, or a re-sweep silently creates a duplicate instead of updating
last_seen. Had zero direct test coverage across all 5 platforms.
"""

from __future__ import annotations

from backend.platforms.facebook.discovery_engine import \
    normalize_url as fb_normalize_url
from backend.platforms.facebook.discovery_engine import profile_id as fb_profile_id
from backend.platforms.instagram.analysis_engine import \
    normalize_url as ig_normalize_url
from backend.platforms.instagram.analysis_engine import \
    username_of as ig_username_of
from backend.platforms.telegram.analysis_engine import \
    normalize_url as tg_normalize_url
from backend.platforms.telegram.analysis_engine import \
    username_of as tg_username_of
from backend.platforms.tiktok.analysis_engine import \
    normalize_url as tt_normalize_url
from backend.platforms.tiktok.analysis_engine import \
    username_of as tt_username_of
from backend.platforms.twitter.analysis_engine import \
    handle_of as tw_handle_of
from backend.platforms.twitter.analysis_engine import \
    normalize_url as tw_normalize_url
from backend.platforms.youtube.analysis_engine import channel_ref
from backend.platforms.youtube.analysis_engine import \
    normalize_url as yt_normalize_url


class TestFacebook:
    def test_scheme_less_input_gets_https(self):
        assert fb_normalize_url("facebook.com/adanigroup") == "https://www.facebook.com/adanigroup"

    def test_fb_com_short_domain_aliases_to_www_facebook_com(self):
        assert fb_normalize_url("https://fb.com/adanigroup") == "https://www.facebook.com/adanigroup"

    def test_fb_me_short_domain_aliases_too(self):
        assert fb_normalize_url("https://fb.me/adanigroup") == "https://www.facebook.com/adanigroup"

    def test_query_string_is_preserved(self):
        assert fb_normalize_url("https://www.facebook.com/profile.php?id=123") == \
            "https://www.facebook.com/profile.php?id=123"

    def test_empty_input_returns_empty_string(self):
        assert fb_normalize_url("") == ""

    def test_profile_php_id_extracts_the_numeric_id(self):
        assert fb_profile_id("https://www.facebook.com/profile.php?id=61555326597220") == "61555326597220"

    def test_people_slash_name_slash_id_extracts_the_numeric_id(self):
        assert fb_profile_id("https://www.facebook.com/people/Adani-Group/100012345678901") == "100012345678901"

    def test_vanity_slug_is_the_id_when_no_numeric_form_present(self):
        assert fb_profile_id("https://www.facebook.com/AdaniOnline") == "AdaniOnline"

    def test_bad_segments_are_never_mistaken_for_an_identity(self):
        for bad in ("pages", "groups", "profile.php", "people", "watch", "reel", "share"):
            assert fb_profile_id(f"https://www.facebook.com/{bad}") == "", bad

    def test_no_path_returns_empty_id_not_the_hostname(self):
        assert fb_profile_id("https://www.facebook.com/") == ""

    def test_groups_slash_id_extracts_the_numeric_group_id(self):
        # the bare /groups directory link (no id) is correctly rejected by
        # the bad-segments test above -- a REAL group URL has a numeric id
        # as its second segment, which is a genuine identity, not noise
        assert fb_profile_id("https://www.facebook.com/groups/152272458887295/") == "152272458887295"

    def test_groups_slash_id_with_no_trailing_slash(self):
        assert fb_profile_id("https://www.facebook.com/groups/152272458887295") == "152272458887295"


class TestInstagram:
    def test_scheme_less_input_gets_https(self):
        assert ig_normalize_url("instagram.com/adanigroup") == "https://www.instagram.com/adanigroup/"

    def test_host_variants_alias_to_www_instagram_com(self):
        # the code's check is a plain `"instagram" in host` substring test
        assert ig_normalize_url("https://m.instagram.com/adanigroup") == "https://www.instagram.com/adanigroup/"

    def test_trailing_slash_is_always_present_on_a_real_path(self):
        assert ig_normalize_url("https://www.instagram.com/adanigroup") == "https://www.instagram.com/adanigroup/"
        assert ig_normalize_url("https://www.instagram.com/adanigroup/") == "https://www.instagram.com/adanigroup/"

    def test_empty_path_has_no_trailing_slash_added_twice(self):
        assert ig_normalize_url("https://www.instagram.com") == "https://www.instagram.com/"

    def test_username_of_strips_at_sign(self):
        assert ig_username_of("https://www.instagram.com/@adanigroup/") == "adanigroup"

    def test_bad_segments_are_not_usernames(self):
        for bad in ("p", "reel", "reels", "explore", "stories", "accounts", "direct", "tv"):
            assert ig_username_of(f"https://www.instagram.com/{bad}/") == "", bad

    def test_empty_input_returns_empty_username(self):
        assert ig_username_of("") == ""


class TestTwitter:
    def test_twitter_dot_com_aliases_to_x_dot_com(self):
        assert tw_normalize_url("https://twitter.com/cyfirma") == "https://x.com/cyfirma"

    def test_x_dot_com_stays_x_dot_com(self):
        assert tw_normalize_url("https://x.com/cyfirma") == "https://x.com/cyfirma"

    def test_scheme_less_input_gets_https(self):
        assert tw_normalize_url("x.com/cyfirma") == "https://x.com/cyfirma"

    def test_handle_of_strips_at_sign(self):
        assert tw_handle_of("https://x.com/@cyfirma") == "cyfirma"

    def test_bad_segments_are_not_handles(self):
        for bad in ("home", "search", "explore", "notifications", "messages",
                    "i", "settings", "compose", "intent"):
            assert tw_handle_of(f"https://x.com/{bad}") == "", bad

    def test_no_path_returns_empty_handle(self):
        assert tw_handle_of("https://x.com/") == ""


class TestYouTube:
    def test_youtu_be_short_domain_aliases_to_www_youtube_com(self):
        assert yt_normalize_url("https://youtu.be/somechannel") == "https://www.youtube.com/somechannel"

    def test_scheme_less_input_gets_https(self):
        assert yt_normalize_url("youtube.com/@AdaniGroup") == "https://www.youtube.com/@AdaniGroup"

    def test_channel_slash_id_is_recognised_as_kind_id(self):
        assert channel_ref("https://www.youtube.com/channel/UCabc123") == ("id", "UCabc123")

    def test_at_handle_is_recognised_as_kind_handle(self):
        assert channel_ref("https://www.youtube.com/@AdaniGroup") == ("handle", "@AdaniGroup")

    def test_legacy_c_slash_name_is_a_handle(self):
        assert channel_ref("https://www.youtube.com/c/AdaniGroup") == ("handle", "AdaniGroup")

    def test_legacy_user_slash_name_is_a_handle(self):
        assert channel_ref("https://www.youtube.com/user/AdaniGroup") == ("handle", "AdaniGroup")

    def test_bare_segment_falls_back_to_a_handle(self):
        assert channel_ref("https://www.youtube.com/AdaniGroup") == ("handle", "AdaniGroup")

    def test_empty_path_returns_empty_kind_and_value(self):
        assert channel_ref("https://www.youtube.com/") == ("", "")

    def test_percent_encoded_handle_is_decoded(self):
        kind, value = channel_ref("https://www.youtube.com/%40AdaniGroup")
        assert (kind, value) == ("handle", "@AdaniGroup")


class TestTelegram:
    def test_at_prefix_shortcut_becomes_a_t_me_url(self):
        assert tg_normalize_url("@adanigroup") == "https://t.me/adanigroup"

    def test_scheme_less_input_gets_https(self):
        assert tg_normalize_url("t.me/adanigroup") == "https://t.me/adanigroup"

    def test_legacy_domains_alias_to_t_me(self):
        assert tg_normalize_url("https://telegram.me/adanigroup") == "https://t.me/adanigroup"
        assert tg_normalize_url("https://telegram.dog/adanigroup") == "https://t.me/adanigroup"

    def test_username_of_strips_at_sign(self):
        assert tg_username_of("https://t.me/@adanigroup") == "adanigroup"

    def test_bad_segments_are_not_usernames(self):
        for bad in ("c", "joinchat", "addstickers", "share", "proxy", "i"):
            assert tg_username_of(f"https://t.me/{bad}") == "", bad

    def test_s_slash_name_is_the_web_preview_of_the_real_channel(self):
        # https://t.me/s/<name> is Telegram's own web-preview alias --
        # the real identity is the SECOND segment, not "s" itself
        assert tg_username_of("https://t.me/s/adanigroup") == "adanigroup"

    def test_bare_s_with_nothing_after_it_has_no_identity(self):
        assert tg_username_of("https://t.me/s") == ""

    def test_empty_input_returns_empty_username(self):
        assert tg_username_of("") == ""


class TestTikTok:
    def test_scheme_less_input_gets_https(self):
        assert tt_normalize_url("tiktok.com/@adanigroup") == "https://www.tiktok.com/@adanigroup"

    def test_host_variants_alias_to_www_tiktok_com(self):
        assert tt_normalize_url("https://m.tiktok.com/@adanigroup") == "https://www.tiktok.com/@adanigroup"

    def test_trailing_slash_is_stripped(self):
        assert tt_normalize_url("https://www.tiktok.com/@adanigroup/") == "https://www.tiktok.com/@adanigroup"

    def test_empty_input_returns_empty_string(self):
        assert tt_normalize_url("") == ""

    def test_username_of_strips_at_sign(self):
        assert tt_username_of("https://www.tiktok.com/@adanigroup") == "adanigroup"

    def test_video_permalink_is_not_mistaken_for_a_username(self):
        # https://www.tiktok.com/@adanigroup/video/123 -- the first segment
        # IS the real username here, "video" only rejected as a BAD_SEGMENT
        # when it's the FIRST path segment (a link with no @user at all)
        for bad in ("video", "live", "tag", "music", "discover", "upload"):
            assert tt_username_of(f"https://www.tiktok.com/{bad}") == "", bad

    def test_empty_input_returns_empty_username(self):
        assert tt_username_of("") == ""
