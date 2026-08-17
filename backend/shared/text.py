"""Parsing and formatting helpers shared by every platform adapter's
extraction code (payload parsing, name-match scoring, date formatting).
Ported unchanged from `backend/core/text.py`.

Lives in `shared/` because every platform's discovery/analysis code depends
on it, and none of them owns it.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Iterator, Optional
from urllib.parse import ParseResult, urlparse

try:
    from rapidfuzz.fuzz import token_set_ratio as _tsr
    HAVE_RF = True
except ImportError:
    HAVE_RF = False

# oldest plausible timestamp, anything before this is not a real post date
EPOCH_FLOOR = 1075593600

MONTHS = (
    "January February March April May June July August September "
    "October November December"
).split()


def iter_dicts(obj: Any, depth: int = 0) -> Iterator[dict]:
    if depth > 45:
        return
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from iter_dicts(v, depth + 1)
    elif isinstance(obj, list):
        for it in obj:
            yield from iter_dicts(it, depth + 1)


def iter_kv(obj: Any, depth: int = 0) -> Iterator[tuple[str, Any]]:
    if depth > 45:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from iter_kv(v, depth + 1)
    elif isinstance(obj, list):
        for it in obj:
            yield from iter_kv(it, depth + 1)


def find_ints(text: str, keys) -> list[int]:
    out = []
    for k in keys:
        out += [
            int(m.group(1)) for m in re.finditer(rf'"{k}"\s*:\s*(\d{{1,13}})', text)
        ]
    return out


def parse_count(raw: str):
    s = re.sub(r"[\s,]", "", raw.strip())
    m = re.match(r"^([\d.]+)([KMB])?$", s, re.I)
    if not m:
        return None, False
    val, suf = float(m.group(1)), (m.group(2) or "").upper()
    mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suf]
    return int(val * mult), (suf == "" and "." not in m.group(1))


def epoch_to_dt(ts: Any) -> Optional[datetime]:
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return None
    if ts > 10**12:
        ts //= 1000
    if not (EPOCH_FLOOR < ts < int(time.time()) + 86400):
        return None
    return datetime.fromtimestamp(ts, timezone.utc)


def parse_joined(raw: str) -> str:
    """'June 2025' | 'July 16, 2026' -> ISO 'YYYY-MM' or 'YYYY-MM-DD'."""
    raw = raw.strip().rstrip(".")
    for fmt, out in (
        ("%B %d, %Y", "%Y-%m-%d"),
        ("%B %d %Y", "%Y-%m-%d"),
        ("%b %d, %Y", "%Y-%m-%d"),
        ("%B %Y", "%Y-%m"),
        ("%b %Y", "%Y-%m"),
    ):
        try:
            return datetime.strptime(raw, fmt).strftime(out)
        except ValueError:
            continue
    return ""


def fmt_created(iso: str) -> str:
    """ISO -> 'Jun-25' (month-year) or 'DD-MM-YYYY' when a day is known."""
    if not iso:
        return ""
    try:
        if len(iso) == 7:
            dt = datetime.strptime(iso, "%Y-%m")
            return f"{MONTHS[dt.month - 1][:3]}-{dt.strftime('%y')}"
        dt = datetime.strptime(iso, "%Y-%m-%d")
        return dt.strftime("%d-%m-%Y")
    except ValueError:
        return iso


def parse_normalized_url(url: str, extra_schemes: tuple[str, ...] = ()) -> Optional[ParseResult]:
    """Strip whitespace/quotes and default to an https:// scheme; the
    common preamble every platform's own `normalize_url()` builds on before
    applying its own host canonicalization and path formatting. Returns
    None for an empty input, so callers can early-return ""."""
    url = (url or "").strip().strip("\"'")
    if not url:
        return None
    if not url.startswith(("http://", "https://", *extra_schemes)):
        url = "https://" + url
    return urlparse(url)


def normalized_host(parsed: ParseResult) -> str:
    return parsed.netloc.lower().split(":")[0]


def is_place(v: str) -> bool:
    """Reject JSON fragments and other debris that key-scraping drags in."""
    v = (v or "").strip()
    return (
        bool(v)
        and len(v) < 70
        and not re.search(r'[\[\]{}"\\]', v)
        and bool(re.search(r"[A-Za-z]", v))
    )


TOKEN_MATCH = 0.82  # how close two tokens must be to count as the same


def _norm_name(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (s or "").lower()).split())


def _covers(target_token: str, candidate_tokens: set[str]) -> bool:
    """Is this target token present in the candidate, typos allowed?

    Fuzzy on purpose: impersonators misspell deliberately ("Gautamm Adani"),
    and an exact-token rule would score those lowest when they should score
    highest.
    """
    return any(
        t == target_token
        or SequenceMatcher(None, target_token, t).ratio() >= TOKEN_MATCH
        for t in candidate_tokens
    )


def name_score(candidate: str, target: str) -> int:
    """0-100 similarity, weighted by how much of the target is accounted for.

    Token-set alone is not enough: it rates "Gautam" against "Gautam Adani" a
    perfect 100, because one is a subset of the other. Scaling by target
    coverage fixes it while keeping word order irrelevant:

        Adani Gautam          -> 100   (same words, reordered)
        Gautam Adani Official -> 100   (target fully covered, extra words ok)
        Gautamm Adani         ->  ~95  (typo-squat: still a match)
        Gautam                ->   50  (half the target: not a match)
    """
    a, b = _norm_name(candidate), _norm_name(target)
    if not a or not b:
        return 0

    ta, tb = set(a.split()), set(b.split())
    if HAVE_RF:
        base = float(_tsr(a, b))
    else:
        inter = ta & tb
        overlap = 100 * len(inter) / max(min(len(ta), len(tb)), 1)
        seq = (
            100
            * SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
        )
        base = max(overlap, seq)

    coverage = sum(_covers(t, ta) for t in tb) / len(tb)
    return int(base * coverage)
