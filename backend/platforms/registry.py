"""Which platforms exist, and how to load each one's adapter classes.

Static catalog plus lazy class loading only, no Mongo, no filesystem
cookie access itself. Adding a platform is one entry here plus its adapter
package under `platforms/<name>/`; nothing else changes.

`session_state()` imports `engine.sessions` lazily (inside the function,
not at module load) purely to dodge a real circular import: `engine.jobs`
imports this registry to pick adapter classes, and `engine.sessions`
(which this function needs) sits in that same import graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any


@dataclass(frozen=True)
class Platform:
    """A platform and its two phases.

    discovery: keywords -> candidate profile URLs
    analysis:  profile URL -> scored Row
    """

    id: str
    name: str
    analysis_path: str  # "package.module:ClassName"
    discovery_path: str = ""  # empty until a platform grows a discovery phase
    session_path: str = ""  # the browser Session subclass, when browser-based
    cookie_domain: str = ""  # which cookies belong to this platform
    required_cookies: tuple[str, ...] = ()  # proof the session is logged in
    api_key_env: str = ""  # set instead of cookies for key-authed platforms
    env_keys: tuple[str, ...] = ()  # credentials a non-cookie platform needs
    session_blob: str = ""  # a non-cookie session file, e.g. MTProto's
    enabled: bool = True
    # a heads-up for the analyst/ops dashboard about *inherent* fragility,
    # not a failure (that's the incidents module's job), a standing caveat
    # about this platform's own scraping surface. Empty when there is none.
    stability_note: str = ""
    # True when `analysis_path` exists but doesn't actually extract fields.
    # Analysis_path itself is always set (every platform needs a Scraper class),
    # so it can't be used as the "does analysis actually work" signal, this is
    # the real one, surfaced to the frontend so an analyst sees the caveat
    # before running analysis, not after it silently produces nothing.
    analysis_stub: bool = False

    @property
    def can_discover(self) -> bool:
        return bool(self.discovery_path)

    @property
    def uses_api_key(self) -> bool:
        return bool(self.api_key_env)

    @property
    def uses_cookies(self) -> bool:
        return bool(self.session_path) and not self.uses_api_key and not self.env_keys

    def scraper(self) -> Any:
        return _load(self.analysis_path)

    def discoverer(self) -> Any:
        if not self.discovery_path:
            raise KeyError(f"{self.id} has no discovery phase")
        return _load(self.discovery_path)

    def session_cls(self) -> Any:
        if not self.session_path:
            raise KeyError(f"{self.id} has no browser session")
        return _load(self.session_path)


def _load(path: str) -> Any:
    module, cls = path.split(":")
    return getattr(import_module(module), cls)


PLATFORMS: dict[str, Platform] = {
    "facebook": Platform(
        id="facebook",
        name="Facebook",
        analysis_path="backend.platforms.facebook.analysis_engine:Scraper",
        discovery_path="backend.platforms.facebook.discovery_engine:Discovery",
        session_path="backend.platforms.facebook.discovery_engine:FacebookSession",
        cookie_domain="facebook",
        required_cookies=("c_user", "xs"),
        stability_note="GraphQL doc IDs can rotate; falls back to a DOM parse when they do.",
    ),
    "twitter": Platform(
        id="twitter",
        name="X / Twitter",
        analysis_path="backend.platforms.twitter.analysis_engine:Scraper",
        discovery_path="backend.platforms.twitter.discovery_engine:Discovery",
        session_path="backend.platforms.twitter.discovery_engine:TwitterSession",
        cookie_domain="twitter x.com",  # one login spans both hosts
        required_cookies=("auth_token", "ct0"),
    ),
    "instagram": Platform(
        id="instagram",
        name="Instagram",
        analysis_path="backend.platforms.instagram.analysis_engine:Scraper",
        discovery_path="backend.platforms.instagram.discovery_engine:Discovery",
        session_path="backend.platforms.instagram.discovery_engine:InstagramSession",
        cookie_domain="instagram",
        required_cookies=("sessionid", "ds_user_id"),
        stability_note="Falls back to reading the rendered page header when Instagram's "
        "GraphQL profile payload doesn't fire -- brittle to layout changes.",
    ),
    "youtube": Platform(
        id="youtube",
        name="YouTube",
        analysis_path="backend.platforms.youtube.analysis_engine:Scraper",
        discovery_path="backend.platforms.youtube.discovery_engine:Discovery",
        api_key_env="YOUTUBE_API_KEY",
    ),
    "telegram": Platform(
        id="telegram",
        name="Telegram",
        analysis_path="backend.platforms.telegram.analysis_engine:Scraper",
        discovery_path="backend.platforms.telegram.discovery_engine:Discovery",
        env_keys=("TELEGRAM_API_ID", "TELEGRAM_API_HASH"),
        session_blob="telegram.session",
    ),
    "tiktok": Platform(
        id="tiktok",
        name="TikTok",
        analysis_path="backend.platforms.tiktok.analysis_engine:Scraper",
        discovery_path="backend.platforms.tiktok.discovery_engine:Discovery",
        session_path="backend.platforms.tiktok.discovery_engine:TikTokSession",
        cookie_domain="tiktok",
        required_cookies=("sessionid",),
        stability_note="TikTok has no free public API; this reads its own embedded page "
        "JSON (field names reverse-engineered, not confirmed against a live session) "
        "and falls back to a DOM parse when that's stripped or reshaped -- needs live "
        "verification before being trusted the way the other platforms already are.",
    ),
}


def get(platform_id: str) -> Platform:
    if platform_id not in PLATFORMS:
        known = ", ".join(PLATFORMS)
        raise KeyError(f"unknown platform {platform_id!r} -- known: {known}")
    return PLATFORMS[platform_id]


async def session_state(p: Platform) -> str:
    """ready | missing | incomplete.

    A key-authed platform is ready when its key is set; an env-keys
    platform (Telegram) needs those set AND its saved MTProto session. A
    cookie-backed platform is resolved against pooled sessions in Mongo.
    All state checks delegate to manager.state_for to enable DB restoration.
    """
    from backend.sessions import manager as sessions_engine

    return await sessions_engine.state_for(p.id)


async def ready_platforms() -> tuple[list[str], dict[str, str]]:
    """(ready platform ids, {unavailable id: state}) across every enabled
    platform, shared by anything that needs to gate a sweep on "is at
    least one platform usable" without silently dropping why the others
    weren't (the analysis catch-up sweep and the round-robin engine both
    need exactly this)."""
    ready: list[str] = []
    unavailable: dict[str, str] = {}
    for platform_id, plat in PLATFORMS.items():
        if not plat.enabled:
            continue
        state = await session_state(plat)
        (ready.append(platform_id) if state == "ready" else unavailable.setdefault(platform_id, state))
    return ready, unavailable
