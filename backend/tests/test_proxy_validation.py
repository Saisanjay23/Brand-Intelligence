"""Per-session proxy config -- the new Proxy tab's server-side backstop.

`stealth/proxy.py::build_proxy_config` passes `proxy["server"]` straight
through to Playwright's context-launch option unvalidated, so a malformed
value used to surface as a cryptic browser-launch failure three steps into
a job instead of at the moment it was actually configured. `_validate_proxy`
is what catches it at config time instead.

The second thing covered here: a proxy only means anything for platforms
that launch a browser through sessions/manager.py::session_for_job. YouTube
(api-key, a REST call) and Telegram (MTProto via Telethon) never read a
per-session `proxy` field at all -- accepting one for them would store
something that LOOKS configured but is silently inert.
"""

from __future__ import annotations

import pytest

from backend.shared.errors import ConflictError, ValidationError
from backend.sessions.manager import _validate_proxy, set_proxy


# _validate_proxy: shape

def test_requires_a_server():
    with pytest.raises(ValidationError, match="server is required"):
        _validate_proxy({})
    with pytest.raises(ValidationError, match="server is required"):
        _validate_proxy({"server": "   "})


@pytest.mark.parametrize("scheme", ["http", "https", "socks4", "socks5", "socks5h"])
def test_every_playwright_supported_scheme_is_allowed(scheme):
    out = _validate_proxy({"server": f"{scheme}://proxy.example.com:8080"})
    assert out["server"] == f"{scheme}://proxy.example.com:8080"


@pytest.mark.parametrize("server", [
    "ftp://proxy.example.com:21",
    "ssh://proxy.example.com:22",
    "proxy.example.com:8080",       # no scheme at all
    "javascript:alert(1)",
])
def test_rejects_a_disallowed_or_missing_scheme(server):
    with pytest.raises(ValidationError):
        _validate_proxy({"server": server})


def test_rejects_a_server_with_no_port():
    with pytest.raises(ValidationError, match="no port"):
        _validate_proxy({"server": "http://proxy.example.com"})


def test_rejects_a_server_with_no_host():
    with pytest.raises(ValidationError, match="no host"):
        _validate_proxy({"server": "http://:8080"})


def test_strips_unrelated_keys_and_keeps_only_known_ones():
    out = _validate_proxy({
        "server": "http://proxy.example.com:8080",
        "username": "alice", "password": "s3cret", "timezone_id": "America/New_York",
        "unrelated_field": "should not survive", "$where": "1==1",
    })
    assert out == {
        "server": "http://proxy.example.com:8080",
        "username": "alice", "password": "s3cret", "timezone_id": "America/New_York",
    }


def test_blank_username_and_password_are_omitted_not_stored_as_empty_strings():
    out = _validate_proxy({"server": "http://proxy.example.com:8080", "username": "  ", "password": ""})
    assert "username" not in out
    assert "password" not in out


def test_auth_free_proxy_stores_only_the_server():
    assert _validate_proxy({"server": "socks5://proxy.example.com:1080"}) == {
        "server": "socks5://proxy.example.com:1080",
    }


# Set_proxy: platform-kind gate

@pytest.mark.asyncio
async def test_youtube_refuses_a_proxy_it_could_never_use(monkeypatch):
    """YouTube talks to a REST API directly -- no browser, no proxy point."""
    with pytest.raises(ConflictError, match="no per-session browser proxy"):
        await set_proxy("youtube", "any-id", {"server": "http://proxy.example.com:8080"})


@pytest.mark.asyncio
async def test_telegram_refuses_a_proxy_it_could_never_use():
    """Telegram connects via Telethon/MTProto, which has its own separate
    proxy mechanism this field was never wired to."""
    with pytest.raises(ConflictError, match="no per-session browser proxy"):
        await set_proxy("telegram", "mtproto", {"server": "http://proxy.example.com:8080"})


@pytest.mark.asyncio
async def test_youtube_may_still_have_its_proxy_cleared(monkeypatch):
    """Clearing (proxy=None) must never be blocked by the kind-gate -- only
    setting a real one is nonsensical; removing a leftover value must
    always be possible."""
    from backend.sessions import manager as mgr

    called = {}

    async def fake_get_item(platform, session_id):
        return {"id": session_id}

    async def fake_unset(platform, session_id):
        called["unset"] = (platform, session_id)
        return True

    async def fake_status(platform):
        return {"platform": platform}

    monkeypatch.setattr(mgr.sessions_db, "get_item", fake_get_item)
    monkeypatch.setattr(mgr.sessions_db, "unset_proxy", fake_unset)
    monkeypatch.setattr(mgr, "status", fake_status)

    await set_proxy("youtube", "any-id", None)
    assert called["unset"] == ("youtube", "any-id")


@pytest.mark.asyncio
async def test_cookie_platform_accepts_and_validates_a_proxy(monkeypatch):
    from backend.sessions import manager as mgr

    stored = {}

    async def fake_get_item(platform, session_id):
        return {"id": session_id}

    async def fake_update(platform, session_id, **fields):
        stored.update(fields)
        return True

    async def fake_status(platform):
        return {"platform": platform}

    monkeypatch.setattr(mgr.sessions_db, "get_item", fake_get_item)
    monkeypatch.setattr(mgr.sessions_db, "update_item", fake_update)
    monkeypatch.setattr(mgr, "status", fake_status)

    await set_proxy("facebook", "sess1", {"server": "http://proxy.example.com:8080", "username": "a"})
    assert stored["proxy"] == {"server": "http://proxy.example.com:8080", "username": "a"}


@pytest.mark.asyncio
async def test_cookie_platform_rejects_a_malformed_proxy_before_touching_the_db(monkeypatch):
    from backend.sessions import manager as mgr

    async def fake_get_item(platform, session_id):
        return {"id": session_id}

    write_attempted = []
    monkeypatch.setattr(mgr.sessions_db, "get_item", fake_get_item)
    monkeypatch.setattr(mgr.sessions_db, "update_item", lambda *a, **k: write_attempted.append(1))

    with pytest.raises(ValidationError):
        await set_proxy("facebook", "sess1", {"server": "not-a-real-proxy"})
    assert not write_attempted
