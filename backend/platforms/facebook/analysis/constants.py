"""Keys and patterns used to read fields off a profile.

The K_* tuples are GraphQL key names; the RE_* patterns read rendered text.
Both drift when Facebook ships changes -- when a field goes blank across every
profile, suspect these first.
"""

from __future__ import annotations

import re

MAX_FOLLOWERS = 5_000_000_000

K_FOLLOWERS = (
    "follower_count",
    "followers_count",
    "fan_count",
    "subscriber_count",
    "follower_count_int",
)
K_JOINED = (
    "joined_date",
    "profile_creation_time",
    "account_creation_time",
    "date_joined",
    "creation_date",
    "page_creation_date",
    "profile_created_time",
)
K_POST_TIME = ("publish_time", "creation_time", "created_time", "publish_time_ts")
K_LOCATION = (
    "current_city_name",
    "single_line_address",
    "full_address",
    "street_address",
    "city_name",
    "hometown_name",
)
# "short_name" is deliberately absent: it is the first name only, so it scores
# under NAME_THRESHOLD, and it is the key that leaks other entities' names
K_NAME = ("profile_name", "page_name", "name_for_display")
K_PIC = ("profile_picture", "profile_pic_url", "uri", "photo_image")

RE_JOINED = re.compile(
    r"(?:Joined Facebook|Joined|Date joined)\s*[-–—:•]?\s*"
    r"([A-Z][a-z]{2,9}\s+\d{4}|[A-Z][a-z]{2,9}\s+\d{1,2},?\s+\d{4})",
    re.I,
)
RE_FOLLOWERS = re.compile(
    r"([\d][\d.,\s]{0,15}[KMB]?)\s*(?:followers|people follow this)", re.I
)
# one header counter chip, e.g. "154M followers" / "53 friends" / "1.2K likes"
RE_CHIP = re.compile(
    r"^([\d][\d.,\s]{0,15}[KMB]?)\s*"
    r"(followers?|following|friends?|likes?|people follow this)\b",
    re.I,
)
RE_LIVES_IN = re.compile(r"Lives in\s+([^\n·|]{2,70})")
RE_FROM = re.compile(r"\bFrom\s+([^\n·|]{2,70})")
RE_NO_POSTS = re.compile(
    r"(No posts yet|hasn't (?:added|shared|posted)|nothing to show|"
    r"No posts available)",
    re.I,
)
# real profile photos live on scontent*.fbcdn.net; rsrc.php / static.xx / t1.30497-1 is
# Facebook's own chrome, which is where the silhouette placeholder comes from
RE_DEFAULT_PIC = re.compile(
    r"(static.*silhouette|default.*avatar|/rsrc\.php/|static\.xx\.fbcdn\.net|t1\.30497-1|30497-1)",
    re.I,
)
