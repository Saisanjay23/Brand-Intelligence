"""The evidence-avatar media proxy fetches a caller-supplied URL server-side
-- an open SSRF proxy unless every hop is constrained, not just the first
one. `urllib.request.urlopen` follows redirects internally before the
caller ever sees them, so checking only the ORIGINAL url (and re-checking
`resp.geturl()` only after the fetch already happened) let a CDN URL that
redirects to an internal host get fetched before being rejected. These
tests pin that every redirect hop is validated BEFORE being followed.
"""

from __future__ import annotations

import urllib.error

import pytest

from backend.api.profile_routes import _ValidatingRedirectHandler, _validate_fetch_target


def _public_dns(monkeypatch):
    import backend.api.profile_routes as routes
    monkeypatch.setattr(routes, "_resolves_to_public_ip", lambda h: True)


def test_allowed_host_on_a_public_ip_passes(monkeypatch):
    _public_dns(monkeypatch)
    ok, host = _validate_fetch_target("https://scontent.fbcdn.net/x.jpg")
    assert ok is True
    assert host == "scontent.fbcdn.net"


def test_host_outside_the_cdn_allowlist_is_refused(monkeypatch):
    _public_dns(monkeypatch)
    ok, _ = _validate_fetch_target("https://evil.example.com/x.jpg")
    assert ok is False


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "10.0.0.5", "192.168.1.1"])
def test_a_private_or_metadata_address_is_refused_even_with_an_allowed_looking_host(monkeypatch, host):
    """DNS-rebinding defense: even a hostname that WOULD pass the CDN
    allowlist must not resolve to something internal -- 169.254.169.254 is
    the cloud metadata endpoint."""
    import backend.api.profile_routes as routes
    monkeypatch.setattr(routes.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, "", (host, 0))])
    ok, _ = _validate_fetch_target("https://scontent.fbcdn.net/x.jpg")
    assert ok is False


def test_non_default_port_is_refused(monkeypatch):
    _public_dns(monkeypatch)
    ok, _ = _validate_fetch_target("https://scontent.fbcdn.net:8080/x.jpg")
    assert ok is False


def test_non_http_scheme_is_refused(monkeypatch):
    _public_dns(monkeypatch)
    ok, _ = _validate_fetch_target("file:///etc/passwd")
    assert ok is False


class TestRedirectIsValidatedBeforeBeingFollowed:
    """The actual SSRF fix: a redirect Location header must be rejected
    BEFORE urllib.request ever sends a request to it, not after."""

    def test_a_redirect_to_a_disallowed_host_is_refused(self, monkeypatch):
        _public_dns(monkeypatch)
        handler = _ValidatingRedirectHandler()
        with pytest.raises(urllib.error.URLError):
            handler.redirect_request(
                req=None, fp=None, code=302, msg="Found", headers={},
                newurl="http://169.254.169.254/latest/meta-data/",
            )

    def test_a_redirect_to_localhost_is_refused(self, monkeypatch):
        _public_dns(monkeypatch)
        handler = _ValidatingRedirectHandler()
        with pytest.raises(urllib.error.URLError):
            handler.redirect_request(
                req=None, fp=None, code=302, msg="Found", headers={},
                newurl="http://localhost:8001/",
            )

    def test_a_redirect_to_a_still_allowed_cdn_host_is_followed(self, monkeypatch):
        """Confirms the fix doesn't just break every redirect -- a
        legitimate same-CDN redirect (a common CDN pattern) must still work."""
        _public_dns(monkeypatch)
        handler = _ValidatingRedirectHandler()
        # A real (non-None) req is needed past this point since a passing
        # validation falls through to urllib's own redirect_request, which
        # builds a follow-up Request from it -- construct a minimal one
        # rather than mocking the whole urllib machinery.
        import urllib.request
        req = urllib.request.Request("https://scontent.fbcdn.net/original.jpg")
        result = handler.redirect_request(
            req=req, fp=None, code=302, msg="Found",
            headers={}, newurl="https://scontent-alt.fbcdn.net/redirected.jpg",
        )
        assert result.full_url == "https://scontent-alt.fbcdn.net/redirected.jpg"
