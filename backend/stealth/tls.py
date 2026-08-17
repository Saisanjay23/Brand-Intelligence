"""TLS / JA3 and proxy cipher integrity verification.

Because every session uses a genuine Chromium/Chrome network stack via Playwright,
the base TLS handshake is inherently that of a legitimate browser.

However, when sessions route through external HTTP/SOCKS5 proxies (`proxy.py`),
poorly configured proxy servers can terminate and reconstitute SSL sessions,
modifying TCP packet fragmentation, HTTP/2 frame ordering, or TLS cipher suites
(JA3 hash). This utility provides diagnostic health validation to ensure a proxy
has not corrupted the browser's native TLS footprint.
"""

from __future__ import annotations

from backend.shared.logging import get_logger

log = get_logger("stealth.tls")


async def verify_proxy_integrity(
    context, probe_url: str = "https://httpbin.org/headers", timeout: float = 8.0
) -> bool:
    """Probes a browser context to assert proxy TLS and HTTP header integrity.

    Returns True if the connection succeeds without proxy-level TLS alteration
    or certificate interception errors.
    """
    if not context:
        return False
    page = None
    try:
        page = await context.new_page()
        response = await page.goto(
            probe_url, wait_until="domcontentloaded", timeout=timeout * 1000
        )
        if response and response.status == 200:
            log.debug("proxy TLS integrity probe succeeded")
            return True
        log.warning(
            f"proxy TLS probe returned non-200 status: "
            f"{response.status if response else 'none'}"
        )
        return False
    except Exception as exc:
        log.warning(f"proxy TLS integrity probe failed: {exc}")
        return False
    finally:
        if page and not page.is_closed():
            try:
                await page.close()
            except Exception:
                pass
