"""`callback_url` is caller-supplied and this process fetches it with
retries -- that is a server-side request forgery primitive unless it is
constrained. These tests pin the constraint.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from backend.services import webhook_service as wh


@pytest.fixture(autouse=True)
def _no_allowlist(monkeypatch):
    monkeypatch.setattr(wh.settings, "webhook_allowed_hosts", [], raising=False)


def _public_dns(monkeypatch):
    monkeypatch.setattr(wh, "_resolves_to_public_ip", lambda h: True)


def test_empty_callback_is_fine():
    assert wh.validate_callback_url("") == (True, "")


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://internal:70/",
    "ftp://internal/",
    "not-a-url",
])
def test_non_http_schemes_are_refused(url):
    ok, reason = wh.validate_callback_url(url)
    assert ok is False and reason


def test_public_host_is_allowed(monkeypatch):
    _public_dns(monkeypatch)
    assert wh.validate_callback_url("https://hooks.example.com/x") == (True, "")


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "169.254.169.254", "10.0.0.5", "192.168.1.1"])
def test_private_and_metadata_addresses_are_refused(host):
    """169.254.169.254 is the cloud metadata endpoint -- reaching it through
    this service would hand a caller our instance credentials."""
    ok, reason = wh.validate_callback_url(f"http://{host}/hook")
    assert ok is False
    assert "public address" in reason or "allowlist" in reason


def test_allowlist_narrows_to_expected_hosts(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(wh.settings, "webhook_allowed_hosts", ["example.com"], raising=False)

    assert wh.validate_callback_url("https://example.com/hook")[0] is True
    assert wh.validate_callback_url("https://api.example.com/hook")[0] is True  # subdomain
    ok, reason = wh.validate_callback_url("https://evil.com/hook")
    assert ok is False and "allowlist" in reason


def test_allowlist_is_not_a_substring_match(monkeypatch):
    """'notexample.com' must not pass an 'example.com' allowlist."""
    _public_dns(monkeypatch)
    monkeypatch.setattr(wh.settings, "webhook_allowed_hosts", ["example.com"], raising=False)
    assert wh.validate_callback_url("https://notexample.com/hook")[0] is False


def test_signature_is_hmac_over_the_exact_body(monkeypatch):
    monkeypatch.setattr(wh.settings, "webhook_secret", "s3cret", raising=False)
    body = b'{"id":"abc"}'
    header = wh._sign(body)["X-BI-Signature"]
    expected = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert header == f"sha256={expected}"


def test_no_secret_means_no_signature_header(monkeypatch):
    monkeypatch.setattr(wh.settings, "webhook_secret", "", raising=False)
    assert wh._sign(b"{}") == {}


def test_signature_changes_with_the_body(monkeypatch):
    monkeypatch.setattr(wh.settings, "webhook_secret", "s3cret", raising=False)
    assert wh._sign(b'{"a":1}') != wh._sign(b'{"a":2}')
