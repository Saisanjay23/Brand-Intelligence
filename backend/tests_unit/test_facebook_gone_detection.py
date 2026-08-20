"""Scraper.blocked_status' GONE verdict -- the one call that decides a
profile is not worth reading at all.

WHY THIS EXISTS
    Facebook serves the byte-identical string "This content isn't available
    at the moment" for two unrelated situations: a profile that really is
    removed, and a single restricted/deleted POST sitting in a live
    profile's timeline. `RE_GONE` matched that substring anywhere in the
    body text, so any live profile with one restricted item in its feed was
    written off as taken down -- every field discarded on a page that had
    just handed over the name and follower count.

    Measured live against the 8 Facebook profiles this engine had marked
    GONE: 5 were fully alive (343000 / 915 / 193 / 90 / 14 followers, real
    names, real last-post dates). And because GONE is terminal in
    shared/completeness.py, each was also stamped analysis_complete and so
    never retried.

    The strings below are the real captured page text from that run, not
    invented fixtures: the "gone" body is the whole 257-character response
    a removed profile returns, and the "live" body is the shape a real one
    returns with a restricted post in its feed.
"""

from __future__ import annotations

from backend.platforms.facebook.analysis_engine import Harvest, Scraper
from backend.shared.models.row import Row

# The complete body of a genuinely removed profile: notification chrome plus
# the placeholder, and nothing else. No name, no counts, no tabs.
GONE_BODY = (
    "  | Number of unread notifications | 1 | This content isn't available at "
    "the moment | When this happens, it's usually because the owner only "
    "shared it with a small group of people, changed who can see it, or it's "
    "been deleted. | Go to News Feed | Facebook"
)

# A live profile carrying the SAME placeholder for one restricted post.
LIVE_BODY = (
    "  | Number of unread notifications | 1 | الأمير "
    "محمد بن سلمان | "
    "915 followers • 0 following | Message | Follow | Search | "
    "News & media website | More | All | About | Followers | Photos | Mentions "
    "| · | This content isn't available at the moment | When this happens, "
    "it's usually because the owner only shared it with a small group of people."
)


def _harvest(text: str, gql=None) -> Harvest:
    h = Harvest()
    h.text = {"main": text}
    h.gql = gql or []
    return h


def _row() -> Row:
    return Row(url="https://www.facebook.com/profile.php?id=100089843602031", target="MBS")


class TestGoneDetection:
    def test_removed_profile_is_still_reported_gone(self):
        """The genuine case must keep working -- this is not a licence to
        stop trusting the placeholder, only to stop trusting it alone."""
        row = _row()
        assert Scraper.blocked_status(row, row.url, GONE_BODY, _harvest(GONE_BODY)) is True
        assert row.status == "GONE"

    def test_live_profile_with_a_restricted_post_is_not_gone(self):
        """The regression: the same placeholder on a page that also carries
        the profile's own follower line must NOT end the read."""
        row = _row()
        assert Scraper.blocked_status(row, row.url, LIVE_BODY, _harvest(LIVE_BODY)) is False
        assert row.status == "PENDING"  # untouched -- reading proceeds

    def test_follower_count_from_the_payload_alone_defeats_the_placeholder(self):
        """The audience number is often only in the GraphQL payload, never
        rendered as text; that is still proof the profile is there."""
        gql = [{"data": {"user": {"id": "100089843602031", "follower_count": 915}}}]
        row = _row()
        assert Scraper.blocked_status(row, row.url, GONE_BODY, _harvest(GONE_BODY, gql)) is False

    def test_zero_followers_still_counts_as_present(self):
        """A real account with no audience yet is not a removed one. `0` is a
        reading, the same distinction completeness.py::_blank draws."""
        body = GONE_BODY.replace("| 1 |", "| 1 | Some Page | 0 followers |")
        row = _row()
        assert Scraper.blocked_status(row, row.url, body, _harvest(body)) is False

    def test_friends_line_also_counts(self):
        """Personal profiles publish friends where a Page publishes
        followers (see facebook/analysis_engine.py::followers_from_friends)."""
        body = GONE_BODY.replace("| 1 |", "| 1 | Someone | 313 friends |")
        row = _row()
        assert Scraper.blocked_status(row, row.url, body, _harvest(body)) is False

    def test_checkpoint_and_login_still_win_over_the_content_gate(self):
        """A blocked SESSION is not a profile-availability question at all:
        those two branches must fire regardless of what the page carries,
        or a checkpoint page that happens to show a follower count would be
        read as a real profile."""
        row = _row()
        body = LIVE_BODY + " | Confirm Your Identity"
        assert Scraper.blocked_status(row, row.url, body, _harvest(body)) is True
        assert row.status == "CHECKPOINT"

        row2 = _row()
        body2 = LIVE_BODY + " | You must log in to continue"
        assert Scraper.blocked_status(row2, row2.url, body2, _harvest(body2)) is True
        assert row2.status == "LOGIN_REQUIRED"

    def test_an_ordinary_live_page_with_no_markers_is_not_blocked(self):
        row = _row()
        body = "Jane Doe | 42 friends | Photos | About"
        assert Scraper.blocked_status(row, row.url, body, _harvest(body)) is False
        assert row.status == "PENDING"
