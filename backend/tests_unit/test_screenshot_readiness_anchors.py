"""Evidence screenshots must wait for CONTENT, not just for text to exist.

WHAT WAS WRONG
    `wait_for_visible_content` returned as soon as the page carried 200
    characters. Measured live 2026-08-22, that bar is cleared by the site's
    own chrome long before any of the profile's content renders, so every
    evidence capture this project produced showed a correct, complete
    header sitting above a LOADING SPINNER:

        instagram  wait 0.92s   2 images    post grid unrendered
        twitter    wait 2.49s   2 of 4 in-view images decoded, timeline spinning
        facebook   wait 0.07s   -- it was not waiting for anything at all

    For impersonation evidence that is the wrong half of the page to lose.
    The header shows a name and a photo were copied; the posts are what
    show the account is actually in use.

    With each platform's own content anchor passed in:

        instagram  2 -> 14 images, 10 of 10 in-view decoded, grid rendered
        twitter    text 570 -> 2718 chars, 9 -> 24 images, timeline rendered

    Real end-to-end cost with evidence on stayed at roughly 6s per profile
    on both, because by screenshot time the engine has usually already
    waited for that same payload for its own extraction.

These tests assert each engine still PASSES an anchor, since the failure
mode is silent: drop the argument and captures quietly go back to being
spinners while every other test still passes.
"""

from __future__ import annotations

import inspect

from backend.platforms.facebook import analysis_engine as fb
from backend.platforms.instagram import analysis_engine as ig
from backend.platforms.twitter import analysis_engine as tw
from backend.stealth.browser import Session


class TestTheWaitAcceptsAnAnchor:
    def test_signature_still_takes_content_selector(self):
        params = inspect.signature(Session.wait_for_visible_content).parameters
        assert "content_selector" in params
        assert "settle_images_ms" in params

    def test_an_empty_timeline_is_not_punished(self):
        """The anchor is bounded on its own budget so an account with
        genuinely no posts shoots what is there instead of paying the full
        timeout on every capture."""
        params = inspect.signature(Session.wait_for_visible_content).parameters
        assert params["content_timeout_ms"].default > 0


class TestEveryEngineStillPassesOne:
    """Reads the source of each screenshot() rather than calling it, so this
    stays a pure unit test with no browser."""

    def _screenshot_source(self, module) -> str:
        return inspect.getsource(module.Scraper.screenshot)

    def test_instagram_waits_for_a_grid_tile(self):
        src = self._screenshot_source(ig)
        assert "content_selector" in src
        assert "/p/" in src and "/reel/" in src

    def test_twitter_waits_for_a_tweet_cell(self):
        src = self._screenshot_source(tw)
        assert "content_selector" in src
        assert "tweet" in src

    def test_facebook_waits_for_a_post_permalink(self):
        src = self._screenshot_source(fb)
        assert "content_selector" in src
        assert "POST_LINK_SELECTOR" in src

    def test_facebook_reuses_its_existing_live_verified_selector(self):
        """The same hook its last-post reader already uses -- one
        definition, so the two cannot drift apart."""
        assert "/posts/" in fb.POST_LINK_SELECTOR
        assert "story_fbid" in fb.POST_LINK_SELECTOR
        assert fb.POST_LINK_SELECTOR in fb.JS_POST_TIMES
