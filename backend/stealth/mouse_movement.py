"""Passive behavioral interaction simulation (mouse motion and micro-scrolling).

While this engine primarily navigates and reads JSON/GraphQL payloads without
clicking UI elements, modern single-page applications (Meta, X) bind
activity listeners (mousemove, pointerdown, scroll) before delivering sensitive
data or resolving challenges.

This module generates subtle, non-interactive pointer motion across blank areas
of the viewport using cubic Bezier curves and humanized micro-scrolling during 
pauses to satisfy activity heuristics without triggering accidental UI interactions or clicks.
"""

from __future__ import annotations

import asyncio
import math
import random


def cubic_bezier(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    """Calculate the cubic Bezier point at time t (0 <= t <= 1)."""
    return ((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * p1 + 3 * (1 - t) * (t ** 2) * p2 + (t ** 3) * p3


async def humanize_interaction(page, scroll: bool = True, moves: int = 3) -> None:
    """Performs natural mouse motion and subtle scrolling on an open page."""
    try:
        if not page or page.is_closed():
            return

        # Perform natural multi-step mouse movements using Bezier curves
        curr_x, curr_y = random.randint(200, 500), random.randint(200, 500)
        try:
            await page.mouse.move(curr_x, curr_y)
        except Exception:
            # Page might not support mouse control or is transitioning
            return

        for _ in range(max(1, moves)):
            target_x = random.randint(120, 1100)
            target_y = random.randint(120, 700)
            
            # Generate control points for the Bezier curve to add human-like arcing
            cp1_x = curr_x + (target_x - curr_x) * random.uniform(0.1, 0.4) + random.uniform(-100, 100)
            cp1_y = curr_y + (target_y - curr_y) * random.uniform(0.1, 0.4) + random.uniform(-100, 100)
            cp2_x = curr_x + (target_x - curr_x) * random.uniform(0.6, 0.9) + random.uniform(-100, 100)
            cp2_y = curr_y + (target_y - curr_y) * random.uniform(0.6, 0.9) + random.uniform(-100, 100)

            steps = random.randint(15, 30)
            
            try:
                for i in range(1, steps + 1):
                    t = i / steps
                    # Ease out timing
                    eased_t = math.sin(t * math.pi / 2)
                    
                    x = cubic_bezier(eased_t, curr_x, cp1_x, cp2_x, target_x)
                    y = cubic_bezier(eased_t, curr_y, cp1_y, cp2_y, target_y)
                    
                    await page.mouse.move(x, y)
                    await asyncio.sleep(random.uniform(0.005, 0.015))
            except Exception:
                break
            
            await asyncio.sleep(random.uniform(0.08, 0.22))
            curr_x, curr_y = target_x, target_y

        if scroll:
            dy = random.randint(120, 350)
            direction = 1 if random.random() > 0.1 else -1 # Sometimes scroll up
            total_dy = dy * direction
            
            try:
                # Simulate discrete scroll wheel ticks rather than perfectly smooth programmatic scrolling
                scroll_steps = random.randint(5, 12)
                for _ in range(scroll_steps):
                    tick = total_dy / scroll_steps + random.uniform(-10, 10)
                    await page.mouse.wheel(0, tick)
                    await asyncio.sleep(random.uniform(0.02, 0.08))
                
                await asyncio.sleep(random.uniform(0.3, 0.6))
            except Exception:
                pass
    except Exception:
        # Swallow any Playwright navigation/lifecycle exceptions safely
        pass
