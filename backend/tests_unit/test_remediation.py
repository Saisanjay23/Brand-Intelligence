"""The fix instructions carried in an alert.

An alert's whole value is that the reader can act on it without first
doing an investigation. That means every path it names must exist: a
playbook that sends someone to a folder that was renamed six months ago
is worse than one that says nothing, because it burns the reader's trust
along with their time. These tests fail on a file move rather than
letting a dead pointer ship.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services import remediation

REPO = Path(__file__).resolve().parents[2]

# Anything in a playbook that looks like a repo path (as opposed to a UI
# location like "Admin -> Sessions", or a placeholder like "<platform>").
_PATH_RE = re.compile(r"backend/[A-Za-z0-9_./]+|requirements\.txt")


def _paths_in(text: str) -> list[str]:
    return [
        p for p in _PATH_RE.findall(text)
        if "<" not in p and not p.endswith("/")
    ]


class TestEveryPathIsReal:
    def test_paths_named_in_playbooks_exist_on_disk(self):
        missing: list[str] = []
        for error_type in list(remediation._BY_TYPE) + ["SomethingUnknown"]:
            for platform in ("facebook", "twitter", "instagram", "youtube", "tiktok", "telegram"):
                book = remediation.playbook_for(error_type, platform)
                blob = " ".join((book.headline, book.folder, *book.steps))
                for path in _paths_in(blob):
                    if not (REPO / path).exists():
                        missing.append(f"{error_type}/{platform}: {path}")
        assert not missing, "playbooks name paths that no longer exist:\n" + "\n".join(missing)

    def test_platform_engine_folders_exist(self):
        for platform in ("facebook", "twitter", "instagram", "youtube", "tiktok", "telegram"):
            book = remediation.playbook_for("ParserDrift", platform)
            assert (REPO / book.folder).is_dir(), f"{book.folder} is not a directory"


class TestContent:
    def test_every_playbook_has_a_headline_and_steps(self):
        for error_type in remediation._BY_TYPE:
            book = remediation.playbook_for(error_type, "facebook")
            assert book.headline.strip(), f"{error_type} has no headline"
            assert book.steps, f"{error_type} has no steps"

    def test_an_unknown_failure_still_gets_usable_guidance(self):
        book = remediation.playbook_for("NeverSeenBefore", "twitter")
        assert book.steps
        assert book.folder == "backend/platforms/twitter/"

    def test_a_session_failure_does_not_send_anyone_into_the_code(self):
        # the fix is pasting cookies; pointing at an engine folder here
        # would send an operator to read parser code for a day
        book = remediation.playbook_for("SessionInvalid", "facebook")
        assert "Sessions" in book.folder
        assert "no code change" in book.folder.lower()

    def test_a_parser_failure_points_at_that_platforms_own_folder(self):
        book = remediation.playbook_for("FieldExtractionDrift", "instagram")
        assert book.folder == "backend/platforms/instagram/"
