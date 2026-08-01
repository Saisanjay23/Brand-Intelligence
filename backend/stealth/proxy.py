"""Per-session proxy configuration for Playwright's context-level override.

Each pooled session can exit through its own IP instead of every session in
a 100-strong pool sharing the one machine's address, which is itself a
correlation signal across "different" accounts.
"""

from __future__ import annotations

from typing import Optional


def build_proxy_config(proxy: Optional[dict]) -> Optional[dict]:
    """`proxy` is `{"server": ..., "username": ..., "password": ..., "timezone_id": ...}`
    (only `server` required); -> Playwright's `proxy` context-option shape, or
    None when there's nothing to configure."""
    if not proxy or not proxy.get("server"):
        return None
    return {
        "server": proxy["server"],
        **({"username": proxy["username"]} if proxy.get("username") else {}),
        **({"password": proxy["password"]} if proxy.get("password") else {}),
    }
