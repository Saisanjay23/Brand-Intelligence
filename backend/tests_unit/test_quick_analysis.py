import pytest
from backend.services.quick_analysis_service import (
    parse_direct_url,
    quick_analysis_manager,
    to_ddmmyyyy,
    _tri_yes_no,
    QuickAnalysisItem,
    QuickAnalysisJob,
)
from backend.shared.models.row import Row


def test_parse_direct_url():
    # Test valid platforms
    assert parse_direct_url("https://www.facebook.com/acmebrand") == ("facebook", "https://www.facebook.com/acmebrand", "acmebrand")
    assert parse_direct_url("https://instagram.com/acme_inc") == ("instagram", "https://instagram.com/acme_inc", "acme_inc")
    assert parse_direct_url("https://x.com/acme_corp") == ("twitter", "https://x.com/acme_corp", "acme_corp")
    assert parse_direct_url("https://twitter.com/acme_corp") == ("twitter", "https://twitter.com/acme_corp", "acme_corp")
    assert parse_direct_url("https://www.youtube.com/@OfficialAcme") == ("youtube", "https://www.youtube.com/@OfficialAcme", "OfficialAcme")
    assert parse_direct_url("https://t.me/acme_updates") == ("telegram", "https://t.me/acme_updates", "acme_updates")
    assert parse_direct_url("https://www.tiktok.com/@acmetok") == ("tiktok", "https://www.tiktok.com/@acmetok", "acmetok")

    # Test invalid / unsupported URLs
    assert parse_direct_url("https://linkedin.com/in/john") is None
    assert parse_direct_url("https://google.com") is None
    assert parse_direct_url("") is None


def test_to_ddmmyyyy():
    assert to_ddmmyyyy("2026-08-26T12:00:00Z") == "26-08-2026"
    assert to_ddmmyyyy("2026-01-05") == "05-01-2026"
    assert to_ddmmyyyy("") == ""
    assert to_ddmmyyyy(None) == ""


def test_tri_yes_no():
    assert _tri_yes_no(True) == "Yes"
    assert _tri_yes_no(False) == "No"
    assert _tri_yes_no(None) == ""


def test_build_both_formats():
    job = QuickAnalysisJob(
        id="test-job-123",
        created_at=1000.0,
        target_name="Acme Corp",
        official_feed="https://x.com/official_acme",
    )
    item = QuickAnalysisItem(
        id="item-456",
        raw_url="https://x.com/fake_acme",
        url="https://x.com/fake_acme",
        platform="twitter",
        entity_id="fake_acme",
        profile_name="Acme Corp Impersonator",
        followers=1500,
        location="New York, USA",
        bio="Official fake account",
        last_post_date="2026-08-20",
        is_active=True,
        has_logo=True,
        has_name_match=True,
        name_score=95,
        risk_score=9,
        priority="High",
        analysed_at="2026-08-26T15:00:00Z",
    )
    row = Row(
        url=item.url,
        target=job.target_name,
        profile_name=item.profile_name,
        followers=item.followers,
        location=item.location,
        bio=item.bio,
        last_post_iso=item.last_post_date,
    )
    row.has_custom_pic = True
    row.name_score = 95

    quick_analysis_manager._build_both_formats(job, item, row)

    # Validate Incident / Platform format
    inc = item.incident_row
    assert inc["AssetType"] == "Twitter / X"
    assert inc["AssetName"] == "Acme Corp"
    assert inc["Source"] == "https://x.com/fake_acme"
    assert inc["Active (Yes/No)"] == "Yes"
    assert inc["Name (Yes/No)"] == "Yes"
    assert inc["Logo (Yes/No)"] == "Yes"
    assert inc["Location"] == "New York, USA"
    assert inc["Number of Followers"] == 1500
    assert inc["Last Post (DD-MM-YYYY) (Optional)"] == "20-08-2026"
    assert inc["RiskScore"] in (8, 9)

    # Validate Legacy format
    leg = item.legacy_row
    assert "Platform" not in leg
    assert leg["Original Name"] == ""
    assert leg["Original feed"] == ""
    assert leg["IMPERSONATED"] == "https://x.com/fake_acme"
    assert leg["Profile name"] == "Acme Corp Impersonator"
    assert leg["Active (Yes / No)"] == "Yes"
    assert leg["Name (Yes / No)"] == "Yes"
    assert leg["Logo (Yes / No)"] == "Yes"
    assert leg["Followers"] == 1500
    assert leg["Risk Score"] == 9
    assert leg["Date"] == "26-08-2026"


@pytest.mark.asyncio
async def test_quick_analysis_manager_start_and_get():
    urls = [
        "https://www.facebook.com/brand",
        "https://instagram.com/brand",
        "https://invalid-domain.xyz/profile",
    ]
    job_id, skipped = quick_analysis_manager.start_job(urls, target_name="Brand")
    assert job_id != ""
    assert len(skipped) == 1
    assert skipped[0]["url"] == "https://invalid-domain.xyz/profile"

    job_data = quick_analysis_manager.get_job(job_id)
    assert job_data is not None
    assert job_data["id"] == job_id
    assert job_data["total"] == 2
    assert len(job_data["items"]) == 2
    assert job_data["target_name"] == "Brand"

    # Test cancel
    cancelled = quick_analysis_manager.cancel_job(job_id)
    assert cancelled is True
