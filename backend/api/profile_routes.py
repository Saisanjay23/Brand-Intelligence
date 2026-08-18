from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Query

from backend.controllers import profile_controller
from backend.dto.profile_dto import (
    BulkDeleteRequest, BulkPatchRequest, DeletePlatformDataRequest, ExportXlsxRequest, ManualUrlsRequest,
    ProfilePatch, PublishAllRequest,
)
from backend.shared.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(tags=["profiles"])

# Every host a profile's `profile_image_url` can actually point at, going by
# the platforms this engine scrapes (facebook/instagram/twitter/
# youtube/telegram), see backend/platforms/*/discovery_engine.py and
# analysis_engine.py. media-proxy exists only to route around those CDNs'
# hotlink/CORS protection, never to fetch arbitrary caller-supplied URLs, so
# this is a real allowlist, not a formality: without it, `?url=` is an open
# SSRF proxy (internal services, cloud metadata endpoints, etc).
_ALLOWED_IMAGE_HOST_SUFFIXES = (
    "fbcdn.net", "facebook.com",
    "cdninstagram.com", "instagram.com",
    "twimg.com", "x.com", "twitter.com",
    "ytimg.com", "ggpht.com", "googleusercontent.com", "googleapis.com", "youtube.com",
    "telesco.pe", "telegram.org", "cdn-telegram.org", "t.me",
    # TikTok serves avatars off regional signed-CDN hosts
    # (p16-common-sign.tiktokcdn-eu.com, p16-sign.tiktokcdn-us.com,
    # p77-sign-va.tiktokcdn.com, ...). Missing here, every TikTok avatar
    # was fetched and stored correctly by discovery and then refused by
    # this proxy with 400 "Host not allowed" -- so TikTok cards were the
    # only ones in the app that never showed a profile picture.
    "tiktokcdn.com", "tiktokcdn-eu.com", "tiktokcdn-us.com", "tiktokv.com", "tiktok.com",
)


def _allowed_image_host(hostname: str) -> bool:
    h = (hostname or "").lower()
    return any(h == suf or h.endswith("." + suf) for suf in _ALLOWED_IMAGE_HOST_SUFFIXES)


def _resolves_to_public_ip(hostname: str) -> bool:
    """Defense-in-depth against DNS rebinding: even an allowlisted hostname
    must not resolve to a private/loopback/link-local/reserved address."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False
    return True


@router.get("/profiles")
async def list_profiles(
    client_id: str, status: Optional[str] = None, phase: Optional[str] = None,
    platform: Optional[str] = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    include_held: bool = False, keyword: Optional[str] = None,
    entity_type: Optional[str] = None,
    # Every filter below is applied server-side, BEFORE limit/offset. They
    # were previously browser-side over whatever page happened to be loaded,
    # while `total` and the pager still reflected the unfiltered query, so
    # filtering across 500 rows showed only the matches inside page 1 and
    # still claimed 500 results.
    priority: Optional[str] = Query(None, pattern="^(High|Medium|Low)$"),
    match_level: Optional[str] = Query(None, pattern="^(high|medium|low)$"),
    keyword_match_type: Optional[str] = Query(None, pattern="^(individual|domain)$"),
    search: Optional[str] = Query(None, max_length=200),
    # Analysis-phase Published/Unpublished tab. Omitted -> both.
    published: Optional[bool] = None,
) -> dict:
    return await profile_controller.list_profiles(
        client_id, status=status, phase=phase, platform=platform, limit=limit, offset=offset,
        include_held=include_held, keyword=keyword, entity_type=entity_type,
        priority=priority, match_level=match_level, keyword_match_type=keyword_match_type,
        search=search, published=published,
    )


def _validate_fetch_target(url: str) -> tuple[bool, str]:
    """(ok, hostname). One place both the initial request AND every
    redirect hop run through, see _ValidatingRedirectHandler below."""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if (
        parsed.scheme not in ("http", "https")
        or not hostname
        or not _allowed_image_host(hostname)
        or parsed.port not in (None, 80, 443)
        or not _resolves_to_public_ip(hostname)
    ):
        return False, hostname
    return True, hostname


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """`urllib.request.urlopen` follows redirects BEFORE the caller ever
    gets a chance to inspect them, the previous code checked the
    hostname of the ORIGINAL url, then only re-checked `resp.geturl()`
    AFTER the redirected request had already been sent. A CDN URL that
    302s to `http://169.254.169.254/...` (or any other internal host) was
    therefore fetched first and rejected second, the SSRF request had
    already gone out by the time the check ran. This subclass runs the
    exact same allowlist/public-IP validation on every `Location` header
    BEFORE following it, so a disallowed hop is refused instead of fetched
    then discarded.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, hostname = _validate_fetch_target(newurl)
        if not ok:
            raise urllib.error.URLError(f"redirect to disallowed host {hostname!r} refused: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@router.get("/profiles/media-proxy")
async def proxy_image(url: str):
    import asyncio
    from fastapi.responses import Response
    from fastapi import HTTPException

    if not url or not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid url")

    ok, hostname = _validate_fetch_target(url)
    if not ok:
        raise HTTPException(status_code=400, detail="Host not allowed")

    def fetch():
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": "https://www.instagram.com/",
            },
        )
        # A dedicated opener with the validating handler, every redirect
        # this request follows is checked BEFORE being followed, not after.
        opener = urllib.request.build_opener(_ValidatingRedirectHandler)
        try:
            with opener.open(req, timeout=5) as resp:
                # Belt and suspenders: re-validate the URL we actually
                # landed on too, in case a future urllib change ever
                # bypasses redirect_request for some redirect class.
                final_host = urlparse(resp.geturl()).hostname or ""
                if not _allowed_image_host(final_host) or not _resolves_to_public_ip(final_host):
                    return None, None
                return resp.read(), resp.headers.get("Content-Type", "image/jpeg")
        except Exception:
            return None, None

    data, content_type = await asyncio.to_thread(fetch)
    if not data:
        raise HTTPException(status_code=404, detail="Image could not be fetched")
    if not (content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="Not an image")
    return Response(content=data, media_type=content_type)


@router.get("/profiles/coverage")
async def profile_coverage(client_id: str, platform: Optional[str] = None) -> dict:
    """"Did we actually check everything?", approved vs analysed vs still
    owed vs permanently blocked. See profile_service.coverage."""
    return await profile_controller.coverage(client_id, platform)


# Storage-cost maintenance, operator-triggered (or wire into your own
# external scheduler), deliberately NOT run automatically by this app. See
# profile_repository.cleanup_stale_pending / archive_stale_rejected for the
# exact safety reasoning behind what each one does and doesn't touch.
@router.post("/profiles/maintenance/cleanup-stale-pending")
async def cleanup_stale_pending(days: int = Query(60, ge=1)) -> dict:
    return await profile_controller.cleanup_stale_pending(days)


@router.post("/profiles/maintenance/archive-stale-rejected")
async def archive_stale_rejected(days: int = Query(180, ge=1)) -> dict:
    return await profile_controller.archive_stale_rejected(days)


@router.get("/profiles/{profile_id}/screenshot")
async def profile_screenshot(profile_id: str, download: bool = False):
    """The evidence capture taken while analysis was reading this profile.

    Served through this route rather than as a static mount so that (a) the
    stored value stays an opaque lookup key that never leaves the server as
    a real path, and (b) a missing capture answers 404 with a reason instead
    of 500. The capture itself lives in Mongo (GridFS, see
    database/repositories/evidence_repository.py), not on this server's
    disk, so there is no local file to contain access to in the first place.

    `?download=true` switches Content-Disposition to attachment, for
    attaching to a takedown request.
    """
    from fastapi.responses import Response

    data, filename = await profile_controller.screenshot(profile_id)
    disposition = "attachment" if download else "inline"
    return Response(
        content=data, media_type="image/png",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            # evidence for a given analysis run is immutable, a re-analysis
            # writes a new capture and bumps screenshot_at, and the client
            # revalidates on that. Short cache so a re-run still shows up
            # promptly in an open tab.
            "Cache-Control": "private, max-age=60",
        },
    )


@router.get("/profiles/{profile_id}")
async def get_profile(profile_id: str) -> dict:
    return await profile_controller.get_profile(profile_id)


@router.patch("/profiles/{profile_id}")
async def patch_profile(profile_id: str, body: ProfilePatch) -> dict:
    return await profile_controller.patch_profile(profile_id, body)


@router.post("/profiles/{profile_id}/publish")
async def publish_profile(profile_id: str) -> dict:
    return await profile_controller.publish_profile(profile_id)


@router.post("/profiles/publish-all")
async def publish_all_profiles(body: PublishAllRequest) -> dict:
    return await profile_controller.publish_all_profiles(body)


@router.post("/profiles/bulk-patch")
async def bulk_patch_profiles(body: BulkPatchRequest) -> dict:
    return await profile_controller.bulk_patch_profiles(body)


@router.post("/profiles/bulk-delete")
async def delete_profiles(body: BulkDeleteRequest) -> dict:
    return await profile_controller.delete_profiles(body)


@router.post("/profiles/delete-platform-data")
async def delete_platform_data(body: DeletePlatformDataRequest) -> dict:
    return await profile_controller.delete_platform_data(body)


@router.post("/profiles/add-urls")
async def add_manual_urls(body: ManualUrlsRequest) -> dict:
    return await profile_controller.add_manual_urls(body)


@router.post("/profiles/export-xlsx")
async def export_xlsx(body: ExportXlsxRequest):
    from io import BytesIO

    from fastapi import HTTPException
    from fastapi.responses import Response
    from openpyxl import Workbook

    if not body.rows:
        raise HTTPException(status_code=400, detail="no rows to export")

    import re

    # same formula-injection concern as the CSV export (CWE-1236): Excel
    # auto-detects a formula from a cell's leading character regardless of
    # how the file was produced, so a scraped value like `=cmd|'/c calc'!A1`
    # (an impersonator's own display name/bio) is just as dangerous written
    # through openpyxl as it is in a hand-rolled CSV. Same mitigation: a
    # leading `'` neutralizes it without changing the visible text.
    # Numeric values and numeric strings (like followers, risk score) are
    # preserved as real int/float so Excel doesn't show "number stored as text" green marks.
    def _safe(v: object) -> object:
        if v is None:
            return ""
        if isinstance(v, (int, float, bool)):
            return v
        s = str(v).strip()
        if not s:
            return ""
        if s == "0" or (s.lstrip("-").isdigit() and not (len(s) > 1 and s.startswith("0"))):
            try:
                return int(s)
            except ValueError:
                pass
        elif re.match(r"^-?\d+\.\d+$", s):
            try:
                return float(s)
            except ValueError:
                pass
        return f"'{s}" if s[:1] in ("=", "+", "-", "@") else s

    wb = Workbook()
    ws = wb.active
    ws.title = "export"
    cols = list(body.rows[0].keys())
    ws.append(cols)
    for row in body.rows:
        ws.append([_safe(row.get(c)) for c in cols])

    buf = BytesIO()
    wb.save(buf)
    filename = (body.filename or "export.xlsx").replace('"', "")
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
