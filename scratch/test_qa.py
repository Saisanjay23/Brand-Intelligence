import asyncio
import os
from backend.services.quick_analysis_service import quick_analysis_manager
import backend.main  # ensure config and app initializes if needed

async def run():
    urls = [
        "https://t.me/unitybankkz",
        "https://x.com/UnityBank",
        "https://x.com/UnityBank1",
        "https://www.facebook.com/profile.php?id=61559724291689"
    ]
    res = await quick_analysis_manager.start(urls, "Unity Bank", "")
    print(f"Started job: {res.job_id}")
    while True:
        status = await quick_analysis_manager.get_job(res.job_id)
        if status.status in ["done", "failed", "cancelled"]:
            for item in status.items:
                print(f"URL: {item.url} -> Name: {item.profile_name}, Followers: {item.followers}, Location: {item.location}, Risk: {item.risk_score}, Status: {item.status}")
                if item.error:
                    print(f"Error: {item.error}")
            break
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(run())
