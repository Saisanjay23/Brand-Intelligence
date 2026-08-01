"""Webhook delivery: when a job reaches a terminal state, POST its final
summary to the `callback_url` the caller supplied at job creation.

Fire-and-forget from the caller's perspective (JobManager schedules this as
a background task, never awaits it inline) -- a slow or unreachable
callback endpoint must never hold up the job's own bookkeeping. Polling
`GET /jobs/{id}` remains the source of truth either way; this is a
convenience push on top of it.
"""

from __future__ import annotations

import asyncio

import aiohttp

from backend.config.settings import settings
from backend.shared.logging import get_logger

log = get_logger("services.webhook")


async def dispatch(job) -> None:
    if not job.callback_url:
        return
    payload = job.to_dict()
    timeout = aiohttp.ClientTimeout(total=settings.webhook_timeout_sec)
    for attempt in range(1, settings.webhook_max_retries + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(job.callback_url, json=payload) as resp:
                    if resp.status < 300:
                        log.info(f"job {job.id}: callback delivered to {job.callback_url} ({resp.status})")
                        return
                    log.warning(f"job {job.id}: callback attempt {attempt} to {job.callback_url} returned {resp.status}")
        except Exception as e:
            log.warning(f"job {job.id}: callback attempt {attempt} to {job.callback_url} failed: {type(e).__name__}: {e}")
        if attempt < settings.webhook_max_retries:
            await asyncio.sleep(min(2**attempt, 30))
    log.error(f"job {job.id}: callback to {job.callback_url} gave up after {settings.webhook_max_retries} attempt(s)")
