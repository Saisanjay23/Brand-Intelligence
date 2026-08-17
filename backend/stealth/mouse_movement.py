"""Passive behavioral interaction simulation (mouse motion and micro-scrolling).

While this engine primarily navigates and reads JSON/GraphQL payloads without
clicking UI elements, modern single-page applications (Meta, X) bind
activity listeners (mousemove, pointerdown, scroll) before delivering sensitive
data or resolving challenges.

This module generates subtle, non-interactive pointer motion across blank areas
of the viewport and humanized micro-scrolling during pauses to satisfy activity
heuristics without triggering accidental UI interactions or clicks.
"""

from __future__ import annotations

import asyncio
import random


async def humanize_interaction(page, scroll: bool = True, moves: int = 3) -> None:
    """Performs natural mouse motion and subtle scrolling on an open page."""
    try:
        if not page or page.is_closed():
            return

        # Perform natural multi-step mouse movements across neutral coordinates
        curr_x, curr_y = random.randint(200, 500), random.randint(200, 500)
        try:
            await page.mouse.move(curr_x, curr_y)
        except Exception:
            # Page might not support mouse control or is transitioning
            return

        for _ in range(max(1, moves)):
            target_x = random.randint(120, 1100)
            target_y = random.randint(120, 700)
            steps = random.randint(8, 18)
            try:
                await page.mouse.move(target_x, target_y, steps=steps)
            except Exception:
                break
            await asyncio.sleep(random.uniform(0.08, 0.22))
            curr_x, curr_y = target_x, target_y

        if scroll:
            dy = random.randint(120, 350)
            try:
                await page.evaluate(f"window.scrollBy({{top: {dy}, behavior: 'smooth'}})")
                await asyncio.sleep(random.uniform(0.3, 0.6))
            except Exception:
                pass
    except Exception:
        # Swallow any Playwright navigation/lifecycle exceptions safely
        pass
