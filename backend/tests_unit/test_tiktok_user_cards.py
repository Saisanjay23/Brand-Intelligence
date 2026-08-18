"""TikTok search returns two populations in one payload, and discovery was
keeping the wrong one.

Live-captured on 2026-08-18 against a real logged-in session, a
`/api/search/general/full/` response for "reliance" carried:

  * `type: 4` -> a `user_list` of `user_info` nodes: the accounts TikTok
    itself considers a NAME MATCH for the query. For "reliance" those were
    `reliance`, `reliance163` and `re1iance` -- the last a digit-swap
    impersonation. These are the entire point of impersonation discovery.
  * `type: 1` x12 -> video items, whose `author` is merely someone who
    posted a clip mentioning the word.

`user_info` is snake_case (`unique_id`, `follower_count`, `avatar_thumb`
as a url_list dict, verification as a non-empty `custom_verify` string);
`author` is camelCase (`uniqueId`, `followerCount`, `avatarLarger`,
`verified` as a bool). The parser only understood camelCase, so every name
match was silently dropped and the twelve bystanders were reported as the
result. These tests pin both dialects, the ordering between them, and the
viewer-identity exclusion.
"""

from __future__ import annotations

from backend.platforms.tiktok.discovery_engine import (
    iter_users, user_from_node, viewer_username,
)


# Shapes below mirror the live capture; irrelevant keys trimmed.
def _user_card(unique_id: str, nickname: str = "", **extra) -> dict:
    card = {
        "unique_id": unique_id,
        "nickname": nickname or unique_id,
        "uid": "7454639806711694368",
        "sec_uid": "MS4wLjABAAAAoGv1lEsOrR6ti4OXyl6a9wfN",
        "follower_count": 991,
        "total_favorited": 17681,
        "signature": "",
        "custom_verify": "",
        "enterprise_verify_reason": "",
        "avatar_thumb": {
            "uri": "tos-useast2a-avt-0068-euttp/0ab48d9",
            "url_list": ["https://p16.tiktokcdn.com/a.jpeg"],
        },
    }
    card.update(extra)
    return card


def _video(author_unique_id: str) -> dict:
    return {
        "type": 1,
        "item": {
            "id": "7300000000000000000",
            "desc": "a clip",
            "author": {"uniqueId": author_unique_id, "nickname": author_unique_id,
                       "avatarLarger": "https://p16.tiktokcdn.com/b.jpeg"},
            "stats": {"followerCount": 12},
        },
    }


def _search_response(cards: list[dict], authors: list[str]) -> dict:
    data: list[dict] = []
    if cards:
        data.append({"type": 4, "user_list": [{"user_info": c} for c in cards]})
    data.extend(_video(a) for a in authors)
    return {"status_code": 0, "data": data, "has_more": 1}


class TestSnakeCaseUserInfoIsParsed:
    def test_user_card_yields_a_user(self):
        u = user_from_node(_user_card("reliance163", "Reliance"))
        assert u is not None
        assert u.username == "reliance163"
        assert u.nickname == "Reliance"
        assert u.url == "https://www.tiktok.com/@reliance163"

    def test_snake_case_counts_are_read(self):
        u = user_from_node(_user_card("x", follower_count=4235, total_favorited=90))
        assert u is not None
        assert u.follower_count == 4235
        assert u.heart_count == 90

    def test_avatar_arrives_as_a_url_list_dict(self):
        u = user_from_node(_user_card("x"))
        assert u is not None
        assert u.avatar == "https://p16.tiktokcdn.com/a.jpeg"
        assert u.has_custom_pic is True

    def test_avatar_dict_with_no_usable_url_is_not_a_picture(self):
        u = user_from_node(_user_card("x", avatar_thumb={"uri": "abc", "url_list": []}))
        assert u is not None
        assert u.avatar == ""
        assert u.has_custom_pic is False

    def test_verification_is_a_string_field_not_a_bool(self):
        assert user_from_node(_user_card("x", custom_verify="Verified account")).verified is True
        assert user_from_node(_user_card("x", enterprise_verify_reason="Business")).verified is True
        # empty strings are the common case and must NOT read as verified
        assert user_from_node(_user_card("x")).verified is False

    def test_entity_id_prefers_uid_when_there_is_no_camelcase_id(self):
        u = user_from_node(_user_card("x"))
        assert u is not None
        assert u.entity_id == "7454639806711694368"

    def test_camelcase_author_still_parses_unchanged(self):
        u = user_from_node(
            {"uniqueId": "creator", "nickname": "Creator", "verified": True,
             "avatarLarger": "https://p16.tiktokcdn.com/b.jpeg"},
            {"followerCount": 500},
        )
        assert u is not None
        assert (u.username, u.follower_count, u.verified) == ("creator", 500, True)


class TestNameMatchesSurviveAndLead:
    def test_user_cards_are_no_longer_dropped(self):
        """THE regression: for "reliance" these three were the only real
        candidates and the parser returned none of them."""
        blob = _search_response(
            [_user_card("reliance"), _user_card("reliance163"), _user_card("re1iance")],
            ["patrickbetdavid", "stoicwisdomquotes", "deepdatenews"],
        )
        found = {u.username for u in iter_users(blob)}
        assert {"reliance", "reliance163", "re1iance"} <= found

    def test_accounts_come_before_video_authors(self):
        blob = _search_response([_user_card("reliance163")],
                                ["bystander1", "bystander2"])
        order = [u.username for u in iter_users(blob)]
        assert order[0] == "reliance163", order

    def test_match_kind_distinguishes_the_two_populations(self):
        blob = _search_response([_user_card("brandco")], ["someone"])
        kinds = {u.username: u.match_kind for u in iter_users(blob)}
        assert kinds["brandco"] == "account"
        assert kinds["someone"] == "author"

    def test_a_payload_with_no_user_card_block_still_yields_authors(self):
        # the common case: most queries return type-1 items only
        blob = _search_response([], ["a", "b"])
        assert [u.username for u in iter_users(blob)] == ["a", "b"]

    def test_an_account_also_present_as_an_author_is_not_duplicated(self):
        blob = _search_response([_user_card("brandco")], ["brandco", "other"])
        names = [u.username for u in iter_users(blob)]
        assert names.count("brandco") == 1
        # and it keeps the richer account classification
        kinds = {u.username: u.match_kind for u in iter_users(blob)}
        assert kinds["brandco"] == "account"


class TestViewerIdentityIsNotAResult:
    """The logged-in account is embedded in every rendered page, so it was
    being saved as a discovered impersonator of every keyword, on every
    sweep -- and ranked first, since the hydration payload is read before
    any search response arrives."""

    def test_reads_the_app_context_handle(self):
        blob = {"__DEFAULT_SCOPE__": {"webapp.app-context": {
            "user": {"uniqueId": "user476175099"}}}}
        assert viewer_username(blob) == "user476175099"

    def test_snake_case_variant(self):
        blob = {"__DEFAULT_SCOPE__": {"webapp.app-context": {
            "user": {"unique_id": "someone"}}}}
        assert viewer_username(blob) == "someone"

    def test_logged_out_or_unexpected_shape_yields_empty(self):
        assert viewer_username({}) == ""
        assert viewer_username({"__DEFAULT_SCOPE__": {}}) == ""
        assert viewer_username({"__DEFAULT_SCOPE__": {"webapp.app-context": {}}}) == ""
        assert viewer_username(None) == ""
        assert viewer_username("not a dict") == ""


class TestResultsScrollTargetsTheRealContainer:
    """TikTok's results live in `<main id="grid-main">`, an inner overflow
    container -- the document itself never scrolls (scrollHeight ==
    innerHeight, window.scrollY pinned at 0). Scrolling the window moved
    nothing, so the lazy-loader never fired and every sweep stopped after
    the one page that arrives with the initial render, then reported
    `stalled` as though the results had run out.

    Measured live for "reliance": 15 users with the window scroll, 135
    with the container scroll (11 pages, cursor 12 -> 132, ending on the
    payload's own has_more: 0). A full job went 29 -> 267 profiles.

    Pinned here because the difference is invisible from the code -- both
    spellings "work", one just silently returns a tenth of the results.
    """

    def test_scroll_helper_targets_the_overflow_container(self):
        from backend.platforms.tiktok.discovery_engine import JS_SCROLL_RESULTS

        assert "#grid-main" in JS_SCROLL_RESULTS
        assert "scrollTop" in JS_SCROLL_RESULTS

    def test_scroll_helper_keeps_a_window_fallback(self):
        # a future layout that does scroll the document must still work
        from backend.platforms.tiktok.discovery_engine import JS_SCROLL_RESULTS

        assert "window.scrollTo" in JS_SCROLL_RESULTS

    def test_the_sweep_does_not_scroll_the_window_directly(self):
        import inspect

        from backend.platforms.tiktok.discovery_engine import Discovery

        src = inspect.getsource(Discovery.sweep)
        assert "window.scrollTo" not in src, (
            "sweep must scroll via JS_SCROLL_RESULTS; a bare window.scrollTo "
            "is a no-op on TikTok and caps results at one page"
        )
        assert "JS_SCROLL_RESULTS" in src
