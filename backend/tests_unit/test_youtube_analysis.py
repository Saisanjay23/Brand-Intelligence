from backend.shared.models.row import Row
from backend.platforms.youtube.analysis_engine import Scraper, channel_ref


def test_channel_ref_variations():
    assert channel_ref("https://www.youtube.com/@techbrand") == ("handle", "@techbrand")
    assert channel_ref("https://www.youtube.com/@techbrand/videos") == ("handle", "@techbrand")
    assert channel_ref("https://www.youtube.com/@techbrand/featured") == ("handle", "@techbrand")
    assert channel_ref("https://www.youtube.com/channel/UC1234567890123456789012") == ("id", "UC1234567890123456789012")
    assert channel_ref("https://www.youtube.com/channel/UC1234567890123456789012/about") == ("id", "UC1234567890123456789012")
    assert channel_ref("https://www.youtube.com/UC1234567890123456789012") == ("id", "UC1234567890123456789012")
    assert channel_ref("https://www.youtube.com/c/TechBrandCustom") == ("handle", "TechBrandCustom")
    assert channel_ref("https://www.youtube.com/user/TechBrandUser") == ("handle", "TechBrandUser")
    assert channel_ref("https://www.youtube.com/TechBrandDirect") == ("handle", "TechBrandDirect")


def test_fill_extracts_profile_name_from_multiple_sources():
    # Case 1: snippet.title present
    row = Row(url="https://www.youtube.com/channel/UC123", target="Brand")
    ch = {
        "id": "UC123",
        "snippet": {"title": "Official Brand Channel", "country": "US"},
        "statistics": {"subscriberCount": "1000", "videoCount": "5"},
    }
    Scraper.fill(row, ch)
    assert row.profile_name == "Official Brand Channel"
    assert row.profile_id == "UC123"

    # Case 2: snippet.title absent, snippet.channelTitle present
    row2 = Row(url="https://www.youtube.com/channel/UC456", target="Brand")
    ch2 = {
        "id": "UC456",
        "snippet": {"channelTitle": "Fallback Channel Title"},
    }
    Scraper.fill(row2, ch2)
    assert row2.profile_name == "Fallback Channel Title"

    # Case 3: snippet.title absent, brandingSettings.channel.title present
    row3 = Row(url="https://www.youtube.com/channel/UC789", target="Brand")
    ch3 = {
        "id": "UC789",
        "brandingSettings": {"channel": {"title": "Branding Title"}},
    }
    Scraper.fill(row3, ch3)
    assert row3.profile_name == "Branding Title"

    # Case 4: only customUrl handle present
    row4 = Row(url="https://www.youtube.com/channel/UC999", target="Brand")
    ch4 = {
        "id": "UC999",
        "snippet": {"customUrl": "@customhandle"},
    }
    Scraper.fill(row4, ch4)
    assert row4.profile_name == "@customhandle"
