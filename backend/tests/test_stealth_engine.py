"""Unit tests for the upgraded robust stealth engine components -- pure, no Mongo
or live network connections needed. Verifies dynamic fingerprinting, Client Hints,
JS runtime native masking, transparent resource fulfilling, and behavioral timing.
"""

from __future__ import annotations

import pytest
from backend.stealth.fingerprint import get_identity, UA
from backend.stealth.headers import build_user_agent_data, build_extra_headers
from backend.stealth.navigator_spoofing import build_init_js, INIT_JS
from backend.stealth.browser import Session, TRANSPARENT_GIF, EMPTY_FONT
from backend.stealth.human import Human
from backend.stealth.tls import verify_proxy_integrity
from backend.stealth.mouse_movement import humanize_interaction


def test_fingerprint_identity_stability():
    """Verify session identity returns coherent, stable device specifications."""
    id1 = get_identity("fb_account_001")
    id2 = get_identity("fb_account_001")
    id3 = get_identity("youtube_002")

    assert id1 == id2
    assert id1 != id3
    assert id1["ua"] == UA
    assert "Chrome" in id1["ua"]
    assert id1["hardware_concurrency"] in (8, 12, 16)
    assert id1["device_memory"] in (8, 16)
    assert isinstance(id1["viewport"], dict)
    assert "width" in id1["viewport"]


def test_headers_client_hints_generation():
    """Verify Client Hints Sec-CH-UA structure and OS platform masking."""
    ch = build_user_agent_data(major_version="134", full_version="134.0.6998.35", platform="Windows")
    assert ch["platform"] == "Windows"
    assert ch["mobile"] is False
    assert len(ch["brands"]) == 3
    assert any(b["brand"] == "Google Chrome" and b["version"] == "134" for b in ch["brands"])
    assert any(b["brand"] == "Google Chrome" and b["version"] == "134.0.6998.35" for b in ch["full_version_list"])


def test_extra_http_headers_alignment():
    """Verify HTTP language header alignment with locale."""
    headers = build_extra_headers("en-GB")
    assert headers["Accept-Language"] == "en-GB,en;q=0.9"

    headers_simple = build_extra_headers("en")
    assert headers_simple["Accept-Language"] == "en;q=0.9"


def test_no_upgrade_insecure_requests_header():
    """Deliberately absent -- Playwright's extra_http_headers is context-wide,
    so this header used to land on cross-origin CDN subresource requests too
    (not just navigation, which is all a real browser sends it on). Confirmed
    live: Facebook's CDN rejects the CORS preflight for every JS/CSS bundle
    its client-side app needs when this header is present, so the page never
    gets past its own loading splash -- every evidence screenshot this engine
    had captured for Facebook was that exact splash because of this one
    header."""
    assert "Upgrade-Insecure-Requests" not in build_extra_headers("en-US")


def test_navigator_spoofing_init_script_content():
    """Verify init JavaScript includes native function masking and hardware overrides."""
    js = build_init_js(hardware_concurrency=12, device_memory=16)
    assert "function get ${name}() { [native code] }" in js
    assert "webdriver" in js
    assert "hardwareConcurrency" in js and "12" in js
    assert "deviceMemory" in js and "16" in js
    assert "window.chrome" in js
    assert INIT_JS is not None and len(INIT_JS) > 100

    # Assert Advanced Defense 1: CDP Variable Scrubbing
    assert "cdc_" in js and "__playwright" in js
    assert "getOwnPropertyNames" in js

    # Assert Advanced Defense 2: Mocking Media Devices & Speech Synthesis
    assert "enumerateDevices" in js and "Internal Microphone" in js
    assert "speechSynthesis" in js and "getVoices" in js

    # Assert Advanced Defense 3: WebRTC Local STUN/ICE Candidate Masking
    assert "RTCPeerConnection" in js and "addIceCandidate" in js

    # Assert Advanced Defense 4: Battery & Broadband Network Information APIs
    assert "connection" in js and "effectiveType: '4g'" in js
    assert "getBattery" in js and "dischargingTime: Infinity" in js


class MockRoute:
    def __init__(self):
        self.fulfilled_with = None
        self.aborted = False
        self.continued = False

    async def fulfill(self, **kwargs):
        self.fulfilled_with = kwargs

    async def abort(self):
        self.aborted = True

    async def continue_(self):
        self.continued = True


class MockRequest:
    def __init__(self, resource_type):
        self.resource_type = resource_type


@pytest.mark.asyncio
async def test_session_resource_filter_fulfilling_images_and_fonts():
    """Verify images and fonts are silently fulfilled with valid dummy bytes instead of aborting."""
    route = MockRoute()
    req = MockRequest("image")
    await Session._filter(route, req)
    assert route.fulfilled_with is not None
    assert route.fulfilled_with["status"] == 200
    assert route.fulfilled_with["content_type"] == "image/gif"
    assert route.fulfilled_with["body"] == TRANSPARENT_GIF

    route_font = MockRoute()
    req_font = MockRequest("font")
    await Session._filter(route_font, req_font)
    assert route_font.fulfilled_with is not None
    assert route_font.fulfilled_with["status"] == 200
    assert route_font.fulfilled_with["content_type"] == "font/woff2"
    assert route_font.fulfilled_with["body"] == EMPTY_FONT

    route_css = MockRoute()
    req_css = MockRequest("stylesheet")
    await Session._filter(route_css, req_css)
    assert route_css.continued is True
    assert route_css.aborted is False


@pytest.mark.asyncio
async def test_human_warmup_delay():
    """Verify human pacing orientation timing succeeds cleanly."""
    h = Human()
    nap = await h.warmup_delay(scale=0.01)
    assert nap > 0.0
    assert h.actions == 0


@pytest.mark.asyncio
async def test_mouse_movement_and_tls_safety():
    """Verify interaction and proxy checks degrade safely on empty or closed contexts."""
    await humanize_interaction(None, scroll=True)
    res = await verify_proxy_integrity(None)
    assert res is False
