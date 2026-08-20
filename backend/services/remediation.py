"""The fix instructions that go in an alert: what broke, and the exact
steps to repair it, per failure type and platform.

`incident_service.diagnose()` already answers "cause" and "fix" in one
sentence each. That is the right length for a dashboard row and too short
for a 2am email: "the scraper's field-reading code likely needs updating"
does not tell anyone which folder to open. This module carries the rest --
ordered steps, naming the real files -- so the email is a work order
rather than a notification.

Every path below is a real file in this repo. They are asserted against
the filesystem by tests_unit/test_remediation.py, so a future file move
breaks a test instead of silently shipping an email that sends an
engineer to a path that no longer exists.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Playbook:
    """What this failure means and what to do about it."""

    headline: str  # one line, plain English, goes in the subject/banner
    steps: tuple[str, ...] = ()  # ordered, imperative
    folder: str = ""  # where the work happens


_SESSION_STEPS = (
    "Open the Brand Intelligence dashboard -> Admin -> Sessions.",
    "Find the entry for this platform; it will be marked failed or cooling down.",
    "Log in to the platform in a normal browser as that account, and confirm it "
    "is not locked, checkpointed, or asking for 2FA. Resolve any challenge there first.",
    "Export fresh cookies for the platform's domain and paste them into the session "
    "entry, then save.",
    "Re-run the affected job. No code change is needed for this failure.",
)

_PARSER_STEPS = (
    "Open the file named under 'Exactly where to fix it' below -- that is the "
    "extraction method that stopped returning data.",
    "Visit one of the example profile/search URLs from the message below in a "
    "logged-in browser, with devtools open on the Network tab.",
    "Compare what the platform now returns against what the code expects: the "
    "field has usually moved to a differently-named object, not disappeared.",
    "Add a read for the NEW location while keeping the existing one as a fallback "
    "(the same shape as the legacy/core fallback already used in "
    "backend/platforms/twitter/discovery_engine.py), so the fix works before and "
    "after the platform finishes rolling the change out.",
    "Add or update the matching case in backend/tests_unit/ with a captured sample "
    "of the new payload, so this exact break is caught next time.",
)


def _engine_folder(platform: str) -> str:
    return f"backend/platforms/{platform}/" if platform and platform != "all" else "backend/platforms/"


_BY_TYPE: dict[str, Playbook] = {
    "SessionInvalid": Playbook(
        headline="The saved login for this platform is no longer valid.",
        steps=_SESSION_STEPS,
        folder="Admin -> Sessions (no code change)",
    ),
    "SessionExpired": Playbook(
        headline="The saved login for this platform has expired.",
        steps=_SESSION_STEPS,
        folder="Admin -> Sessions (no code change)",
    ),
    "CredentialsInvalid": Playbook(
        headline="This platform's API credentials are missing or rejected.",
        steps=(
            "Open Admin -> Sessions and check this platform's API key / credential entry.",
            "Confirm the key is present, not expired, and still enabled in the provider's console.",
            "Paste a working key and save, then re-run the job.",
        ),
        folder="Admin -> Sessions (no code change)",
    ),
    "QuotaExceeded": Playbook(
        headline="This platform's daily API quota is used up.",
        steps=(
            "No code fix is needed -- this resets on the provider's schedule "
            "(YouTube: midnight US Pacific).",
            "If this is happening most days, the run is too large for one key: either "
            "request a quota increase in the provider console, or add a second key and "
            "rotate between them.",
            "To reduce spend per run, check that cheap endpoints are being used where "
            "possible (see the playlistItems-instead-of-search note in "
            "backend/platforms/youtube/discovery_engine.py).",
        ),
        folder="backend/platforms/youtube/",
    ),
    "PlatformBlocked": Playbook(
        headline="The platform is actively refusing this account's requests.",
        steps=(
            "Do NOT immediately retry -- repeated refusals extend the penalty and can "
            "escalate a rate limit into an account lock.",
            "Check Admin -> Sessions: the account should have been quarantined automatically.",
            "Let the cooldown elapse, then retry with a different session from the pool.",
            "If this recurs on every session, the pacing is too aggressive: widen the "
            "delays in backend/config/settings.py (discovery_concurrency, round_robin_slots) "
            "and the per-platform jitter in that platform's discovery engine.",
        ),
        folder="backend/config/settings.py + backend/platforms/<platform>/",
    ),
    "ParserDrift": Playbook(
        headline="The scraper no longer recognises this platform's search results.",
        steps=_PARSER_STEPS,
        folder="",
    ),
    "FieldExtractionDrift": Playbook(
        headline="One specific field stopped extracting, while the rest still work.",
        steps=_PARSER_STEPS,
        folder="",
    ),
    "LastPostExtractionDrift": Playbook(
        headline="The last-post date stopped extracting for most profiles.",
        steps=_PARSER_STEPS
        + (
            "Watch for pinned posts specifically: several platforms surface a pinned "
            "post first with no marker, which reads as a wrong (too recent) date rather "
            "than a missing one.",
        ),
        folder="",
    ),
    "ExtractionDegraded": Playbook(
        headline="The primary extraction method failed; a weaker fallback is covering it.",
        steps=(
            "Output is still correct, but with fewer fields per profile -- this is not urgent, "
            "it is this week's work.",
            "Fix the primary path before the fallback ages out too, using the steps for a "
            "parser change (compare live payload against the file named below).",
        ),
        folder="",
    ),
    "ConfigDrift": Playbook(
        headline="The deployment is misconfigured in a way that silently changes results.",
        steps=(
            "Read the message below -- it names the exact setting or missing dependency.",
            "Apply the change on the server running the pipeline, then restart the service.",
            "This check runs at startup, so a successful restart with no new alert confirms the fix.",
        ),
        folder="backend/config/ + requirements.txt",
    ),
}

_FALLBACK = Playbook(
    headline="An unrecognised failure occurred in the pipeline.",
    steps=(
        "Read the message and blame trail below -- they name the code that failed.",
        "Confirm the platform's session is valid under Admin -> Sessions first; that is "
        "the most common cause of an unfamiliar failure.",
        "If the session is fine, the platform has most likely changed its page or payload "
        "shape and the extraction code needs updating.",
    ),
)


def playbook_for(error_type: str, platform: str = "") -> Playbook:
    """The fix instructions for one incident, with the platform's own
    engine folder filled in when the failure is code-side."""
    book = _BY_TYPE.get((error_type or "").strip(), _FALLBACK)
    if book.folder:
        return book
    return Playbook(book.headline, book.steps, _engine_folder(platform))
