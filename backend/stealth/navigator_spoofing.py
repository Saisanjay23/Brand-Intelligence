"""The one init script every context runs, and why it stops at two overrides.

No canvas / WebGL / audio fingerprint spoofing, and no playwright-stealth.
Those patches are detectable in themselves: overriding those prototypes
reads as a privacy extension, and Facebook responds by never finishing its
render (an infinite spinner), Twitter with "Something went wrong", and
Instagram by not hydrating at all. Less patching survives longer here --
`navigator.webdriver` and enforced visibility are the two overrides that
are actually defensible, because a headless tab that reports itself
hidden gets throttled regardless.
"""

from __future__ import annotations

INIT_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(document, 'visibilityState', {get: () => 'visible',
                                                    configurable: true});
Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
"""
