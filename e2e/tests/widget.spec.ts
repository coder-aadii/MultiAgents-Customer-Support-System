import { test, expect } from "@playwright/test";

test.describe("Sub-phase 2.5: JavaScript Widget", () => {
  test("double init is idempotent with exactly one launcher", async ({ page }) => {
    await page.goto("http://widget:5174");

    // Initially 1 launcher exists
    const launchers = page.locator(".mcss-launcher-btn");
    await expect(launchers).toHaveCount(1);

    // Call init a second time via window.MCSSWidget.init
    await page.evaluate(() => {
      (window as any).MCSSWidget.init({
        apiBaseUrl: "/api/v1",
        title: "Updated MCSS Support",
      });
    });

    // Still exactly 1 launcher exists
    await expect(launchers).toHaveCount(1);
  });

  test("launcher toggles panel and responds to keyboard Escape", async ({ page }) => {
    await page.goto("http://widget:5174");

    const launcher = page.locator(".mcss-launcher-btn");
    await expect(launcher).toHaveAttribute("aria-expanded", "false");

    // Click to open
    await launcher.click();
    await expect(launcher).toHaveAttribute("aria-expanded", "true");

    const panel = page.locator(".mcss-panel");
    await expect(panel).toBeVisible();

    // Press Escape to close
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(launcher).toHaveAttribute("aria-expanded", "false");
  });

  test("plain-text escaping prevents HTML injection in messages", async ({ page }) => {
    await page.goto("http://widget:5174");

    const launcher = page.locator(".mcss-launcher-btn");
    await launcher.click();

    const textarea = page.locator(".mcss-input");
    const sendBtn = page.locator(".mcss-send-btn");

    // Send a message containing HTML markup
    const htmlPayload = '<b id="test-bold">Bold Text</b> <script>window.__injected=true;</script>';
    await textarea.fill(htmlPayload);
    await sendBtn.click();

    // Verify message bubble contains text without creating DOM element #test-bold
    const bubble = page.locator(".mcss-message.visitor .mcss-bubble");
    await expect(bubble).toContainText("Bold Text");

    // Ensure #test-bold does not exist as an HTML element
    const boldElement = page.locator("#test-bold");
    await expect(boldElement).toHaveCount(0);

    // Ensure script was not executed
    const injected = await page.evaluate(() => (window as any).__injected);
    expect(injected).toBeUndefined();
  });

  test("narrow viewport adjusts widget panel to full width", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("http://widget:5174");

    const launcher = page.locator(".mcss-launcher-btn");
    await launcher.click();

    const panel = page.locator(".mcss-panel");
    await expect(panel).toBeVisible();

    const box = await panel.boundingBox();
    expect(box).not.toBeNull();
    if (box) {
      // In narrow viewport, width should take full viewport
      expect(box.width).toBeGreaterThanOrEqual(370);
    }
  });

  test("tokens and credentials are never exposed in URL or localStorage", async ({ page }) => {
    await page.goto("http://widget:5174");
    const launcher = page.locator(".mcss-launcher-btn");
    await launcher.click();

    // Check URL
    expect(page.url()).not.toContain("token=");
    expect(page.url()).not.toContain("bearer");

    // Check localStorage
    const storage = await page.evaluate(() => ({ ...localStorage }));
    expect(storage["mcss_visitor_session_token"]).toBeUndefined();
    expect(storage["session_token"]).toBeUndefined();
  });
});
