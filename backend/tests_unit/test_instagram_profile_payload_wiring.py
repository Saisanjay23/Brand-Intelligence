"""The Instagram profile payload: which responses are read, and what is
read out of them.

WHY THIS FILE EXISTS
    Every one of these assertions guards a defect that was live in
    production on 2026-08-22, and that no existing test could have caught,
    because none of them were parsing bugs. `profile_from()` was already
    correct. `_latest_post()` was already correct. The engine simply never
    handed them the payload that had the data in it, and the result was
    silent: 310 of 310 stored Instagram analysis rows had come from the
    LAST-RESORT rendered-header tier, with a rounded follower count, no
    biography at all, and no last-post date on 30% of them.

    The shapes below are trimmed from real captured responses (a live
    logged-in profile visit), not invented, so they stay honest about what
    Instagram actually sends.
"""

from __future__ import annotations

from backend.platforms.instagram.discovery_engine import (
    PROFILE_ENDPOINTS, profile_from, timeline_latest_post)

# Trimmed from the real `https://www.instagram.com/api/graphql` response.
# The whole profile record lives under `data.user` as TYPED values.
API_GRAPHQL_BODY = {
    "data": {
        "user": {
            "id": "73877322700",
            "pk": "73877322700",
            "username": "adaniparivar",
            "full_name": "Gautam Adani Parivar",
            "follower_count": 79525,
            "following_count": 79,
            "media_count": 462,
            "biography": "Ideal family jahan seva hai sanskaar",
            "city_name": "Ahmedabad",
            "external_url": "https://example.com",
            "is_private": False,
            "is_verified": True,
            "profile_pic_url": "https://instagram.example/real.jpg",
        }
    }
}

# Trimmed from the real `https://www.instagram.com/graphql/query` response.
# Note the ORDER: edges[0] is a PINNED post from 2025-12-25, while the
# account's genuinely newest post sits further down. Note also the foreign
# account in the third edge -- tagged users and suggestions really do ride
# along in this payload.
TIMELINE_BODY = {
    "data": {
        "xdt_api__v1__feed__user_timeline_graphql_connection": {
            "edges": [
                {"node": {"pk": "1", "taken_at": 1766664000,   # 2025-12-25, pinned
                          "user": {"username": "adaniparivar"}}},
                {"node": {"pk": "2", "taken_at": 1787313600,   # 2026-08-21
                          "user": {"username": "adaniparivar"}}},
                {"node": {"pk": "3", "taken_at": 1787400000,   # 2026-08-22, NOT ours
                          "user": {"username": "gautam.adani"}}},
            ]
        }
    }
}


class TestProfileEndpointsCoverage:
    def test_api_graphql_is_listened_to(self):
        """The regression that cost 100% of rows their primary tier.

        `https://www.instagram.com/api/graphql` carries the profile record.
        Dropping this fragment does not break any parser and does not raise
        anything -- it just silently demotes every profile to the DOM
        fallback, which is why it went unnoticed."""
        url = "https://www.instagram.com/api/graphql"
        assert any(e in url for e in PROFILE_ENDPOINTS)

    def test_graphql_query_is_listened_to(self):
        url = "https://www.instagram.com/graphql/query?doc_id=123"
        assert any(e in url for e in PROFILE_ENDPOINTS)

    def test_an_unrelated_url_is_not_matched(self):
        assert not any(e in "https://www.instagram.com/ajax/bz?__a=1"
                       for e in PROFILE_ENDPOINTS)


class TestProfileFromApiGraphql:
    def test_reads_the_whole_record_from_data_user(self):
        u = profile_from(API_GRAPHQL_BODY, "adaniparivar")
        assert u is not None
        assert u.username == "adaniparivar"
        assert u.full_name == "Gautam Adani Parivar"
        assert u.entity_id == "73877322700"

    def test_follower_count_is_the_exact_integer_not_a_rounded_string(self):
        """The DOM tier could only ever offer "79.5K". This is the whole
        reason the API tier is worth having."""
        assert profile_from(API_GRAPHQL_BODY, "adaniparivar").followers == 79525

    def test_biography_is_read(self):
        """Blank on 100% of stored rows before this payload was read."""
        assert "sanskaar" in profile_from(API_GRAPHQL_BODY, "adaniparivar").biography

    def test_city_name_is_read(self):
        """Instagram's only location field, and it is genuinely published."""
        assert profile_from(API_GRAPHQL_BODY, "adaniparivar").city_name == "Ahmedabad"

    def test_a_different_username_is_not_attributed_this_record(self):
        assert profile_from(API_GRAPHQL_BODY, "someoneelse") is None


class TestTimelineLatestPost:
    def test_reads_the_date_the_profile_gate_would_have_discarded(self):
        """`profile_from` cannot parse a timeline payload -- its user nodes
        carry no counts and no biography -- so the timestamps sitting in it
        were being intercepted, parsed and then thrown away."""
        assert profile_from(TIMELINE_BODY, "adaniparivar") is None
        assert timeline_latest_post(TIMELINE_BODY, "adaniparivar") == "2026-08-21"

    def test_pinned_first_edge_does_not_win(self):
        """edges[0] is a pinned 2025-12-25 post. Trusting position instead
        of taking the max would report this account 8 months stale."""
        assert timeline_latest_post(TIMELINE_BODY, "adaniparivar") != "2025-12-25"

    def test_a_foreign_accounts_newer_post_is_not_stolen(self):
        """The newest timestamp in the payload (2026-08-22) belongs to
        gautam.adani, not to the profile being scored. Counting it would
        make a dormant impersonator look active."""
        assert timeline_latest_post(TIMELINE_BODY, "adaniparivar") == "2026-08-21"

    def test_scoping_refuses_an_unrelated_username(self):
        assert timeline_latest_post(TIMELINE_BODY, "nobody-here") == ""

    def test_no_timestamps_yields_empty_never_a_guess(self):
        assert timeline_latest_post(API_GRAPHQL_BODY, "adaniparivar") == ""

    def test_owner_less_payload_still_reads_unscoped(self):
        """Fallback for a shape that carries no owner at all -- better a
        dated post than none, and it can only apply when there is no
        competing owner to confuse it with."""
        blob = {"items": [{"taken_at": 1787313600}]}
        assert timeline_latest_post(blob, "adaniparivar") == "2026-08-21"
