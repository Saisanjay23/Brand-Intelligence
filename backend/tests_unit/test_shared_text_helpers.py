"""Small parsing/formatting primitives in backend/shared/text.py that every
platform's discovery/analysis code calls into (follower counts, join dates,
timestamps, URL parsing). None had direct unit coverage -- each is exercised
only incidentally through whichever platform-specific test happens to feed
it a value that reaches this code path.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from backend.shared.text import (EPOCH_FLOOR, epoch_to_dt, fmt_created,
                                   is_place, normalized_host, parse_count,
                                   parse_joined, parse_normalized_url)


class TestParseCount:
    def test_plain_integer_is_exact(self):
        assert parse_count("1234") == (1234, True)

    def test_comma_separated_integer_is_exact(self):
        assert parse_count("12,345") == (12345, True)

    def test_k_suffix_is_not_exact(self):
        assert parse_count("1.5K") == (1500, False)

    def test_m_suffix_is_not_exact(self):
        assert parse_count("2M") == (2_000_000, False)

    def test_b_suffix_is_not_exact(self):
        assert parse_count("1B") == (1_000_000_000, False)

    def test_lowercase_suffix_is_accepted(self):
        assert parse_count("3k") == (3000, False)

    def test_whitespace_is_stripped(self):
        assert parse_count("  42  ") == (42, True)

    def test_garbage_returns_none_and_false(self):
        assert parse_count("N/A") == (None, False)

    def test_empty_string_returns_none_and_false(self):
        assert parse_count("") == (None, False)

    def test_decimal_without_suffix_is_not_exact(self):
        # a bare decimal (not K/M/B) is unusual but must not be reported
        # as an exact integer reading
        assert parse_count("1.5") == (1, False)


class TestEpochToDt:
    def test_seconds_epoch_converts(self):
        dt = epoch_to_dt(1700000000)
        assert dt == datetime.fromtimestamp(1700000000, tz=timezone.utc)

    def test_milliseconds_epoch_is_detected_and_converted(self):
        # a payload sending ms instead of s produces a 13-digit number;
        # dividing by 1000 must recover the same real-world instant
        dt_ms = epoch_to_dt(1700000000000)
        dt_s = epoch_to_dt(1700000000)
        assert dt_ms == dt_s

    def test_string_input_is_accepted(self):
        assert epoch_to_dt("1700000000") == datetime.fromtimestamp(1700000000, tz=timezone.utc)

    def test_non_numeric_input_returns_none(self):
        assert epoch_to_dt("not-a-timestamp") is None
        assert epoch_to_dt(None) is None

    def test_timestamp_before_facebook_existed_is_rejected(self):
        assert epoch_to_dt(EPOCH_FLOOR - 1) is None

    def test_timestamp_exactly_at_the_floor_is_rejected(self):
        # the check is a strict `>`, not `>=`
        assert epoch_to_dt(EPOCH_FLOOR) is None

    def test_timestamp_just_after_the_floor_is_accepted(self):
        assert epoch_to_dt(EPOCH_FLOOR + 1) is not None

    def test_far_future_timestamp_is_rejected(self):
        assert epoch_to_dt(int(time.time()) + 86400 * 400) is None

    def test_timestamp_a_few_hours_in_the_future_is_still_accepted(self):
        # small clock skew tolerance -- up to a day ahead
        assert epoch_to_dt(int(time.time()) + 3600) is not None


class TestParseJoined:
    def test_month_day_comma_year(self):
        assert parse_joined("July 16, 2026") == "2026-07-16"

    def test_month_day_year_no_comma(self):
        assert parse_joined("July 16 2026") == "2026-07-16"

    def test_abbreviated_month_day_comma_year(self):
        assert parse_joined("Jul 16, 2026") == "2026-07-16"

    def test_month_year_only(self):
        assert parse_joined("June 2025") == "2025-06"

    def test_abbreviated_month_year_only(self):
        assert parse_joined("Jun 2025") == "2025-06"

    def test_trailing_period_is_stripped(self):
        assert parse_joined("June 2025.") == "2025-06"

    def test_unrecognised_format_returns_empty_string(self):
        assert parse_joined("sometime last year") == ""

    def test_empty_string_returns_empty_string(self):
        assert parse_joined("") == ""


class TestFmtCreated:
    def test_full_date_becomes_dd_mm_yyyy(self):
        assert fmt_created("2026-07-16") == "16-07-2026"

    def test_year_month_only_becomes_mon_yy(self):
        assert fmt_created("2025-06") == "Jun-25"

    def test_empty_input_returns_empty_string(self):
        assert fmt_created("") == ""

    def test_unparseable_input_is_returned_unchanged_not_raised(self):
        assert fmt_created("not-a-date") == "not-a-date"


class TestParseNormalizedUrl:
    def test_scheme_less_url_defaults_to_https(self):
        p = parse_normalized_url("example.com/path")
        assert p is not None and p.scheme == "https"

    def test_existing_scheme_is_kept(self):
        p = parse_normalized_url("http://example.com/path")
        assert p is not None and p.scheme == "http"

    def test_quotes_and_whitespace_are_stripped(self):
        p = parse_normalized_url('  "example.com"  ')
        assert p is not None and p.netloc == "example.com"

    def test_empty_input_returns_none(self):
        assert parse_normalized_url("") is None
        assert parse_normalized_url("   ") is None

    def test_extra_scheme_is_recognised_and_not_double_prefixed(self):
        p = parse_normalized_url("tg://resolve?domain=x", extra_schemes=("tg://",))
        assert p is not None and p.scheme == "tg"


class TestNormalizedHost:
    def test_lowercases_the_host(self):
        p = parse_normalized_url("https://Example.COM/path")
        assert normalized_host(p) == "example.com"

    def test_strips_the_port(self):
        p = parse_normalized_url("https://example.com:8443/path")
        assert normalized_host(p) == "example.com"


class TestIsPlace:
    def test_a_real_place_name_is_accepted(self):
        assert is_place("Mumbai, India") is True

    def test_empty_string_is_rejected(self):
        assert is_place("") is False

    def test_json_fragment_debris_is_rejected(self):
        assert is_place('{"city": "Mumbai"}') is False

    def test_overly_long_string_is_rejected(self):
        assert is_place("x" * 70) is False

    def test_a_string_just_under_the_length_limit_is_accepted(self):
        assert is_place("x" * 68 + "A") is True  # 69 chars, < 70

    def test_digits_only_with_no_letters_is_rejected(self):
        # a place name must contain at least one letter -- pure digits are
        # more likely a stray count or id that leaked into a text field
        assert is_place("123456") is False
