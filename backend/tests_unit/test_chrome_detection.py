"""Chrome is found by asking the system, not by a hardcoded path list.

A path list is correct on the machine it was written for and silently
wrong everywhere else: the lookup misses, every session then advertises
FALLBACK_CHROME_FULL, and a UA claiming a version no binary on the host
actually has is a worse tell than not spoofing at all. That failure is
invisible -- nothing errors, the scraping just gets more fingerprintable.

So the order is: explicit override, PATH, Windows registry, known paths,
Playwright's own download.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import backend.stealth.fingerprint as fp


def _uncached():
    """chrome_binary() memoises; tests need a fresh resolution each time."""
    fp._CHROME_BINARY_CACHED = None
    fp._CHROME_BINARY_RESOLVED = False


class TestOverrideWins:
    def test_env_var_is_used_when_it_points_at_a_real_file(self, tmp_path):
        exe = tmp_path / "my-chrome"
        exe.write_text("")
        _uncached()
        with patch.dict(os.environ, {fp.CHROME_BINARY_ENV: str(exe)}):
            assert fp.chrome_binary() == str(exe)
        _uncached()

    def test_env_var_pointing_nowhere_falls_through_rather_than_breaking(self):
        """A typo in the override must not disable browsing entirely."""
        _uncached()
        with patch.dict(os.environ, {fp.CHROME_BINARY_ENV: "/nope/does/not/exist"}), \
             patch.object(fp, "_from_path", return_value="/usr/bin/google-chrome"):
            assert fp.chrome_binary() == "/usr/bin/google-chrome"
        _uncached()

    def test_quoted_env_value_is_accepted(self, tmp_path):
        exe = tmp_path / "chrome.exe"
        exe.write_text("")
        _uncached()
        with patch.dict(os.environ, {fp.CHROME_BINARY_ENV: f'"{exe}"'}):
            assert fp.chrome_binary() == str(exe)
        _uncached()


class TestStrategyOrder:
    def test_path_is_preferred_over_hardcoded_locations(self):
        _uncached()
        with patch.dict(os.environ, {}, clear=False), \
             patch.object(fp, "_from_env", return_value=None), \
             patch.object(fp, "_from_path", return_value="/from/path"), \
             patch.object(fp, "_from_known_paths", return_value="/hardcoded"):
            assert fp.chrome_binary() == "/from/path"
        _uncached()

    def test_known_paths_still_work_when_nothing_else_finds_it(self):
        _uncached()
        with patch.object(fp, "_from_env", return_value=None), \
             patch.object(fp, "_from_path", return_value=None), \
             patch.object(fp, "_from_windows_registry", return_value=None), \
             patch.object(fp, "_from_known_paths", return_value="/hardcoded"):
            assert fp.chrome_binary() == "/hardcoded"
        _uncached()

    def test_playwright_chromium_is_the_last_resort(self):
        _uncached()
        with patch.object(fp, "_from_env", return_value=None), \
             patch.object(fp, "_from_path", return_value=None), \
             patch.object(fp, "_from_windows_registry", return_value=None), \
             patch.object(fp, "_from_known_paths", return_value=None), \
             patch.object(fp, "_from_playwright", return_value="/ms-playwright/chromium"):
            assert fp.chrome_binary() == "/ms-playwright/chromium"
        _uncached()

    def test_none_when_nothing_is_installed(self):
        _uncached()
        for name in ("_from_env", "_from_path", "_from_windows_registry",
                     "_from_known_paths", "_from_playwright"):
            pass
        with patch.object(fp, "_from_env", return_value=None), \
             patch.object(fp, "_from_path", return_value=None), \
             patch.object(fp, "_from_windows_registry", return_value=None), \
             patch.object(fp, "_from_known_paths", return_value=None), \
             patch.object(fp, "_from_playwright", return_value=None):
            assert fp.chrome_binary() is None
        _uncached()


class TestAStrategyCannotBreakDetection:
    def test_a_raising_strategy_is_skipped_not_fatal(self):
        """Registry access, subprocesses and Playwright imports can all
        fail in odd environments; none may take the whole lookup down."""
        _uncached()
        with patch.object(fp, "_from_env", side_effect=RuntimeError("boom")), \
             patch.object(fp, "_from_path", side_effect=OSError("boom")), \
             patch.object(fp, "_from_windows_registry", return_value=None), \
             patch.object(fp, "_from_known_paths", return_value="/still/found"):
            assert fp.chrome_binary() == "/still/found"
        _uncached()


class TestVersionComesFromTheBinary:
    def test_this_machine_detected_a_real_version(self):
        """Not the stale fallback: if this regresses, every session starts
        advertising a version nothing here runs."""
        if fp.chrome_binary() is None:
            return  # no browser installed on this host; nothing to assert
        assert fp.CHROME_VERSION_DETECTED is True
        assert fp.CHROME_FULL_VERSION != fp.FALLBACK_CHROME_FULL
        assert fp.CHROME_MAJOR_VERSION.isdigit()
