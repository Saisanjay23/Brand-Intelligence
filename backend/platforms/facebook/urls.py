"""Facebook URL shapes: normalising, identifying, and tab addresses."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse


def normalize_url(url: str) -> str:
    url = url.strip().strip("\"'")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    host = p.netloc.lower().split(":")[0]
    if "facebook" in host or host in {"fb.com", "fb.me"}:
        host = "www.facebook.com"
    q = f"?{p.query}" if p.query else ""
    return f"https://{host}{p.path}{q}"


def profile_id(url: str) -> str:
    """Returns the numeric id or the vanity slug -- always as a string.

    Normalises first: without a scheme, urlparse puts the host in the path and
    the first segment comes back as "facebook.com".
    """
    url = normalize_url(url)
    if m := re.search(r"profile\.php\?id=(\d+)", url):
        return m.group(1)
    if m := re.search(r"/people/[^/]+/(\d+)", url):
        return m.group(1)
    seg = [s for s in urlparse(url).path.split("/") if s]
    if not seg:
        return ""
    first = seg[0].split("?")[0]
    bad = {"pages", "groups", "profile.php", "people", "watch", "reel", "share"}
    return "" if first.lower() in bad else first


def tab_url(base: str, sk: str) -> str:
    p = urlparse(base)
    if "profile.php" in p.path:
        uid = parse_qs(p.query).get("id", [""])[0]
        return f"https://www.facebook.com/profile.php?id={uid}&sk={sk}"
    return f"https://www.facebook.com{p.path.rstrip('/')}/{sk}"


# fbcdn signs the whole crop range up to `cstp`'s bound, not the specific
# `ctp` size actually requested -- every profile-picture URL Facebook hands
# out (search snippet or profile header alike) asks for a tiny ctp (40-60px)
# while cstp carries the real uploaded photo's native size. Raising ctp to
# match cstp, with the same signature tokens untouched, is what actually
# yields the full-resolution upload rather than the thumbnail -- verified
# live: a 50x50 crop (2.8KB) vs. the same URL bumped to 638x638 (33KB), both
# 200 OK, no new request needed.
_CTP = re.compile(r"ctp=s\d+x\d+")
_CSTP = re.compile(r"cstp=mx(\d+)x(\d+)")
_STP_SIZE = re.compile(r"([sp])\d+x\d+")


def hd_picture_url(url: str) -> str:
    if not url:
        return url
    cstp = _CSTP.search(url)
    if not cstp:
        return url
    w, h = cstp.group(1), cstp.group(2)
    if _CTP.search(url):
        url = _CTP.sub(f"ctp=s{w}x{h}", url)
    url = _STP_SIZE.sub(lambda m: f"{m.group(1)}{w}x{h}", url)
    return url
