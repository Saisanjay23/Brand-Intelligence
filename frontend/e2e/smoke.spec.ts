/**
 * Read-only UI smoke test.
 *
 * The previous version of this suite ran a real discovery job scoped to
 * YouTube only -- safe because YouTube is the one platform with no live
 * logged-in session (an official, key-authed API call), and the old backend
 * let a caller target discovery at one platform specifically.
 *
 * That per-platform targeting no longer exists: this backend's `POST
 * /discovery` always sweeps every platform with a ready session in a single
 * job (see backend/api/routes_discovery.py). There is no way to trigger
 * discovery from the UI without also firing it at whichever of
 * Facebook/Instagram/Twitter happen to have a live session
 * configured -- exactly the real-account risk this project has otherwise
 * been careful to avoid running unattended. Until the backend grows a way to
 * scope a sweep to one platform again, this suite stays read-only: it
 * confirms the app boots and talks to the real backend, but never launches
 * a job.
 */

import { expect, test } from "@playwright/test";

test.describe("app boot smoke test (read-only, no job launched)", () => {
  test("loads, reaches the real backend, and renders all three pages without console errors", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Live Discovery: the platform rail is populated from the real
    // GET /health/platforms -- at least one platform tile must render with
    // a real session_state, proving the request round-tripped.
    await expect(page.locator(".platform-rail-item").first()).toBeVisible({ timeout: 10_000 });

    await page.getByRole("button", { name: "Dashboard" }).click();
    await expect(page.locator(".stat-tile").first()).toBeVisible();

    await page.getByRole("button", { name: "Sessions" }).click();
    await expect(page.locator(".session-card").first()).toBeVisible();

    expect(consoleErrors).toEqual([]);
  });
});
