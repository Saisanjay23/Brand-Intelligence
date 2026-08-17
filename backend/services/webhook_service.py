"""Webhook delivery: when a job reaches a terminal state, POST its final
summary to the `callback_url` the caller supplied at job creation.

Fire-and-forget from the caller's perspective (JobManager schedules this as
a background task, never awaits it inline), a slow or unreachable
callback endpoint must never hold up the job's own bookkeeping. Polling
`GET /jobs/{id}` remains the source of truth either way; this is a
convenience push on top of it.

TWO THINGS THIS IS CAREFUL ABOUT, because `callback_url` is caller-supplied
and this process will fetch it with retries:

1. It is a server-side request forgery primitive unless constrained. The
   engine sits inside a network where "http://169.254.169.254/..." and
   "http://internal-admin:8080/..." resolve to things a caller must not be
   able to reach through us. Every destination is therefore checked to be
   http(s), on an allowed host if an allowlist is configured, and resolving
   only to public addresses, re-checked per attempt, so a DNS entry that
   flips to a private address between retries doesn't get through.

2. The receiver has no way to tell our callback from anything else that
   learned the URL. `X-BI-Signature` is an HMAC-SHA256 over the exact bytes
   sent, so a receiver holding `webhook_secret` can verify it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
from urllib.parse import urlparse

import aiohttp

from backend.config.settings import settings
from backend.shared.logging import get_logger

log = get_logger("services.webhook")


def _host_allowed(hostname: str) -> bool:
    """Empty allowlist means "any public host"; a configured one is exact
    host or subdomain match."""
    allowed = settings.webhook_allowed_hosts
    if not allowed:
        return True
    h = hostname.lower()
    return any(h == a.lower() or h.endswith("." + a.lower()) for a in allowed)


def _resolves_to_public_ip(hostname: str) -> bool:
    """Every address this host resolves to must be publicly routable.

    Checked per delivery attempt rather than once up front: a hostname the
    caller controls can point at a public address for the first check and a
    private one for the actual request.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False
    return True


def validate_callback_url(url: str) -> tuple[bool, str]:
    """(ok, reason). Called at job-creation time so a caller finds out
    immediately that its callback is unusable, instead of the job running to
    completion and the delivery silently failing much later."""
    if not url:
        return True, ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "callback_url must be http or https"
    host = parsed.hostname or ""
    if not host:
        return False, "callback_url has no host"
    if not _host_allowed(host):
        return False, f"callback_url host {host!r} is not in the configured allowlist"
    if not _resolves_to_public_ip(host):
        return False, f"callback_url host {host!r} does not resolve to a public address"
    return True, ""


def _sign(body: bytes) -> dict[str, str]:
    if not settings.webhook_secret:
        return {}
    mac = hmac.new(settings.webhook_secret.encode("utf-8"), body, hashlib.sha256)
    return {"X-BI-Signature": f"sha256={mac.hexdigest()}"}


async def dispatch(job) -> None:
    if not job.callback_url:
        return

    # Serialize ONCE: the signature must cover the exact bytes sent, and
    # re-encoding per attempt could in principle produce different bytes
    # from the same object.
    body = json.dumps(job.to_dict(), default=str).encode("utf-8")
    headers = {"Content-Type": "application/json", **_sign(body)}
    timeout = aiohttp.ClientTimeout(total=settings.webhook_timeout_sec)

    for attempt in range(1, settings.webhook_max_retries + 1):
        ok, reason = validate_callback_url(job.callback_url)
        if not ok:
            log.error(f"job {job.id}: refusing callback to {job.callback_url} -- {reason}")
            return
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    job.callback_url, data=body, headers=headers,
                    # a redirect can hop to a host that would never have
                    # passed the check above, follow none of them
                    allow_redirects=False,
                ) as resp:
                    if resp.status < 300:
                        log.info(f"job {job.id}: callback delivered to {job.callback_url} ({resp.status})")
                        return
                    if 300 <= resp.status < 400:
                        log.error(
                            f"job {job.id}: callback to {job.callback_url} returned a redirect "
                            f"({resp.status}) -- not followed; point callback_url at the final URL"
                        )
                        return
                    log.warning(f"job {job.id}: callback attempt {attempt} to {job.callback_url} returned {resp.status}")
        except Exception as e:
            log.warning(f"job {job.id}: callback attempt {attempt} to {job.callback_url} failed: {type(e).__name__}: {e}")
        if attempt < settings.webhook_max_retries:
            await asyncio.sleep(min(2**attempt, 30))
    log.error(f"job {job.id}: callback to {job.callback_url} gave up after {settings.webhook_max_retries} attempt(s)")
