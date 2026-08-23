"""Rotated cookies must be saved back, and a logged-out jar must not be.

WHY THIS EXISTS
    Accounts were being logged out often, and "the session expired" was
    the wrong diagnosis. Measured on 2026-08-23, the STORED cookies were
    nowhere near expiry:

        facebook  xs, c_user     +364 days
        instagram sessionid      +361 days
        twitter   auth_token/ct0 +158 days

    They were being INVALIDATED, not timing out. `ctx.cookies()` appeared
    in exactly two places in the whole backend -- the manual-login capture
    and auto-login -- so nothing saved cookies back after an ordinary
    discovery or analysis run. These platforms rotate session cookies as
    you browse (X reissues `ct0`, Instagram rolls `sessionid`, Facebook
    refreshes `xs`), so every run replayed an ever-staler jar, and
    replaying a superseded session token is a signal these platforms
    treat as account takeover.

    The dangerous half of the fix is the write itself: a run that got
    logged out mid-way hands back a jar with the auth cookie GONE, and
    saving that over a good stored one would destroy the very session
    this is meant to preserve.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.sessions import manager


FULL_FB = [
    {"name": "c_user", "value": "100", "domain": ".facebook.com", "path": "/"},
    {"name": "xs", "value": "rotated-new", "domain": ".facebook.com", "path": "/"},
    {"name": "datr", "value": "d", "domain": ".facebook.com", "path": "/"},
]
LOGGED_OUT_FB = [
    {"name": "datr", "value": "d", "domain": ".facebook.com", "path": "/"},
    {"name": "fr", "value": "f", "domain": ".facebook.com", "path": "/"},
]


def _updates():
    """Patches the repository write and returns the recording mock."""
    return patch.object(manager.sessions_db, "update_item", new=AsyncMock(return_value=True))


class TestARefreshedJarIsStored:
    @pytest.mark.asyncio
    async def test_a_complete_jar_is_written_back(self):
        with _updates() as upd:
            assert await manager.refresh_cookies("facebook", "s1", FULL_FB) is True
        assert upd.await_count == 1
        kwargs = upd.await_args.kwargs
        names = {c["name"] for c in kwargs["cookies"]}
        assert "xs" in names and "c_user" in names

    @pytest.mark.asyncio
    async def test_the_rotated_value_is_what_lands(self):
        with _updates() as upd:
            await manager.refresh_cookies("facebook", "s1", FULL_FB)
        xs = [c for c in upd.await_args.kwargs["cookies"] if c["name"] == "xs"][0]
        assert xs["value"] == "rotated-new"

    @pytest.mark.asyncio
    async def test_it_stamps_when_it_happened(self):
        with _updates() as upd:
            await manager.refresh_cookies("facebook", "s1", FULL_FB)
        assert "cookies_updated_at" in upd.await_args.kwargs

    @pytest.mark.asyncio
    async def test_it_updates_the_named_pool_row(self):
        with _updates() as upd:
            await manager.refresh_cookies("facebook", "s1", FULL_FB)
        assert upd.await_args.args[:2] == ("facebook", "s1")


class TestALoggedOutJarIsRefused:
    """The whole point of the guard: never overwrite a good session with
    the remains of one that died mid-run."""

    @pytest.mark.asyncio
    async def test_a_jar_missing_the_auth_cookie_is_not_written(self):
        with _updates() as upd:
            assert await manager.refresh_cookies("facebook", "s1", LOGGED_OUT_FB) is False
        assert upd.await_count == 0

    @pytest.mark.asyncio
    async def test_an_empty_jar_is_not_written(self):
        with _updates() as upd:
            assert await manager.refresh_cookies("facebook", "s1", []) is False
        assert upd.await_count == 0

    @pytest.mark.asyncio
    async def test_a_jar_of_another_platforms_cookies_is_not_written(self):
        """Domain filtering leaves nothing, which must read as "no jar",
        not as "here is an empty one to store"."""
        foreign = [{"name": "sessionid", "value": "x", "domain": ".instagram.com", "path": "/"}]
        with _updates() as upd:
            assert await manager.refresh_cookies("facebook", "s1", foreign) is False
        assert upd.await_count == 0


class TestNothingToSaveTo:
    @pytest.mark.asyncio
    async def test_no_session_id_is_a_no_op(self):
        """An anonymous run has no pool row behind it."""
        with _updates() as upd:
            assert await manager.refresh_cookies("facebook", "", FULL_FB) is False
        assert upd.await_count == 0

    @pytest.mark.asyncio
    async def test_a_key_authed_platform_is_a_no_op(self):
        with _updates() as upd:
            assert await manager.refresh_cookies("youtube", "s1", FULL_FB) is False
        assert upd.await_count == 0


class TestTheCallbackFactory:
    def test_it_returns_nothing_without_a_session_id(self):
        assert manager.cookie_saver("facebook", "") is None

    @pytest.mark.asyncio
    async def test_the_callback_is_bound_to_its_session(self):
        cb = manager.cookie_saver("facebook", "s7")
        with patch.object(manager, "refresh_cookies", new=AsyncMock()) as ref:
            await cb(FULL_FB)
        ref.assert_awaited_once_with("facebook", "s7", FULL_FB)


class TestTheBrowserHandsThemOverBeforeClosing:
    @pytest.mark.asyncio
    async def test_stop_invokes_the_callback_with_the_live_jar(self):
        from backend.stealth.browser import Session

        got = {}

        async def sink(cookies):
            got["cookies"] = cookies

        class _Ctx:
            closed = False

            async def cookies(self):
                return FULL_FB

            async def close(self):
                _Ctx.closed = True

        sess = Session.__new__(Session)
        sess.ctx, sess.browser, sess._pw = _Ctx(), None, None
        sess.on_cookies = sink
        await sess.stop()
        assert got["cookies"] == FULL_FB
        assert _Ctx.closed is True

    @pytest.mark.asyncio
    async def test_a_failing_callback_still_closes_the_browser(self):
        """A leaked Chromium is worse than a missed cookie save."""
        from backend.stealth.browser import Session

        async def boom(_cookies):
            raise RuntimeError("mongo down")

        class _Ctx:
            closed = False

            async def cookies(self):
                return FULL_FB

            async def close(self):
                _Ctx.closed = True

        sess = Session.__new__(Session)
        sess.ctx, sess.browser, sess._pw = _Ctx(), None, None
        sess.on_cookies = boom
        await sess.stop()
        assert _Ctx.closed is True

    @pytest.mark.asyncio
    async def test_no_callback_is_the_default_and_changes_nothing(self):
        from backend.stealth.browser import Session

        class _Ctx:
            closed = False

            async def cookies(self):
                raise AssertionError("must not be read when no sink is set")

            async def close(self):
                _Ctx.closed = True

        sess = Session.__new__(Session)
        sess.ctx, sess.browser, sess._pw = _Ctx(), None, None
        sess.on_cookies = None
        await sess.stop()
        assert _Ctx.closed is True
