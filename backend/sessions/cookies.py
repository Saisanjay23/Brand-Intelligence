"""Session cookie loading: JSON exports, Netscape cookies.txt, header strings."""

from __future__ import annotations

import json
import re
from pathlib import Path

SAMESITE = {
    "no_restriction": "None",
    "unspecified": "Lax",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
    "": "Lax",
}


def normalize_cookies(items: list[dict], domain: str = "") -> list[dict]:
    """Normalise a cookie export into Playwright's shape.

    `domain` keeps one platform's cookies out of another's context. It is
    matched loosely because a single login spans several hosts, x.com and
    twitter.com, instagram.com and i.instagram.com, so callers pass the
    distinctive stem ("twitter", "instagram") rather than an exact host.
    """
    wanted = [d for d in domain.replace(",", " ").split() if d]
    out = []
    for c in items:
        if not isinstance(c, dict) or "name" not in c:
            continue
        dom = c.get("domain") or (f".{wanted[0]}.com" if wanted else "")
        if wanted and not any(w in dom for w in wanted):
            continue
        item = {
            "name": c["name"],
            "value": str(c.get("value", "")),
            "domain": dom if dom.startswith(".") else "." + dom.lstrip("."),
            "path": c.get("path") or "/",
            "secure": bool(c.get("secure", True)),
            "httpOnly": bool(c.get("httpOnly", False)),
            "sameSite": SAMESITE.get(str(c.get("sameSite", "")).lower(), "Lax"),
        }
        exp = c.get("expirationDate") or c.get("expires")
        try:
            if exp and float(exp) > 0:
                item["expires"] = int(float(exp))
        except (TypeError, ValueError):
            pass
        out.append(item)
    return out


def load_cookies(spec: str, domain: str = "") -> list[dict]:
    """Read a cookie file (or a literal cookie string) into Playwright cookies.

    Accepts a JSON array, a Playwright storage_state object, Netscape
    cookies.txt, or a raw `Cookie:` header. Pass `domain` to keep only the
    cookies belonging to one platform.
    """
    p = Path(spec)
    text = p.read_text(encoding="utf-8", errors="replace") if p.exists() else spec
    s = text.lstrip()
    if s.startswith("{"):
        obj = json.loads(text)
        return normalize_cookies(obj.get("cookies") or [obj], domain)
    if s.startswith("["):
        return normalize_cookies(json.loads(text), domain)
    if "\t" in text or re.search(r"^\S+\s+(TRUE|FALSE)\s", text, re.M):
        out = []
        for line in text.splitlines():
            raw = line[10:] if line.startswith("#HttpOnly_") else line
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            f = raw.split("\t")
            if len(f) < 7:
                f = re.split(r"\s{2,}", raw.strip())
            if len(f) < 7:
                continue
            d, _fl, path, sec, exp, name, val = f[:7]
            out.append(
                {
                    "name": name,
                    "value": val,
                    "domain": d,
                    "path": path,
                    "secure": sec.upper() == "TRUE",
                    "httpOnly": line.startswith("#HttpOnly_"),
                    "expirationDate": exp,
                }
            )
        return normalize_cookies(out, domain)
    # raw header string
    out = []
    for part in text.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out.append({"name": k.strip(), "value": v.strip()})
    return normalize_cookies(out, domain)
