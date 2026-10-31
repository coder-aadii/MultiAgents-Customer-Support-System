import { test, expect } from "@playwright/test";

test.describe("Sub-phase 2.6: Multi-Party Conversation Lifecycle", () => {
  test("visitor text send → staff inbox sees it → take over → reply → visitor sees reply → close rejects sends", async ({
    browser,
  }) => {
    // -------------------------------------------------------------------------
    // 1. Setup isolated browser contexts and console error listeners
    // -------------------------------------------------------------------------
    const visitorContext = await browser.newContext();
    const staffContext = await browser.newContext();

    const visitorPage = await visitorContext.newPage();
    const staffPage = await staffContext.newPage();

    const visitorErrors: string[] = [];
    const staffErrors: string[] = [];

    visitorPage.on("console", (msg) => {
      if (msg.type() === "error") {
        visitorErrors.push(msg.text());
      }
    });

    staffPage.on("console", (msg) => {
      if (msg.type() === "error") {
        staffErrors.push(msg.text());
      }
    });

    // -------------------------------------------------------------------------
    // 2. Visitor opens widget on port 5174 and sends message
    // -------------------------------------------------------------------------
    await visitorPage.goto("http://widget:5174");

    const launcher = visitorPage.locator(".mcss-launcher-btn");
    await expect(launcher).toBeVisible();
    await launcher.click();

    // Welcome message appears
    await expect(visitorPage.locator(".mcss-message.assistant")).toBeVisible();

    const testTimestamp = Date.now();
    const visitorName = `Alice ${testTimestamp}`;
    const visitorMessage = `Hello, I need assistance with shipment #${testTimestamp}`;

    // Share contact details to uniquely identify this conversation
    await visitorPage.locator(".mcss-contact-toggle").click();
    await visitorPage.locator('input[type="text"].mcss-contact-input').fill(visitorName);
    await visitorPage.locator(".mcss-contact-save-btn").click();
    await expect(visitorPage.locator("text=Details saved")).toBeVisible();

    const visitorInput = visitorPage.locator(".mcss-input");
    await visitorInput.fill(visitorMessage);
    await visitorPage.locator(".mcss-send-btn").click();

    // Visitor sees their sent message in the timeline
    await expect(
      visitorPage.locator(`.mcss-message.visitor:has-text("${visitorMessage}")`)
    ).toBeVisible();

    // -------------------------------------------------------------------------
    // 3. Staff logs in on port 5173, navigates to Inbox
    // -------------------------------------------------------------------------
    await staffPage.goto("http://admin-web:5173/login");
    await staffPage.fill("#email", "staff@example.com");
    await staffPage.fill("#password", "StaffPass123!");
    await staffPage.click("button[type='submit']");

    await expect(staffPage).toHaveURL(/\/inbox/);

    // Wait for conversation with visitor's name to appear in the list (polling every 3s)
    const convItem = staffPage.locator(`[data-testid="conversation-item"]:has-text("${visitorName}")`);
    await expect(convItem).toBeVisible({ timeout: 10_000 });
    await convItem.click();

    // Staff sees visitor's message in the timeline
    await expect(
      staffPage.locator(`div[role="log"]:has-text("${visitorMessage}")`)
    ).toBeVisible();

    // -------------------------------------------------------------------------
    // 4. Staff takes over conversation
    // -------------------------------------------------------------------------
    const takeOverBtn = staffPage.locator("button:has-text('Take over')");
    await expect(takeOverBtn).toBeVisible();
    await takeOverBtn.click();

    // Status updates to Staff active
    await expect(
      staffPage.locator("text=Staff active").first()
    ).toBeVisible({ timeout: 5_000 });

    // -------------------------------------------------------------------------
    // 5. Staff replies to visitor
    // -------------------------------------------------------------------------
    const staffReply = `Hello! Staff operator here. I am investigating shipment #${testTimestamp}.`;
    const replyComposer = staffPage.locator("#reply-composer");
    await replyComposer.fill(staffReply);
    await staffPage.locator("button[type='submit']:has-text('Send')").click();

    // Staff sees reply in their own timeline
    await expect(
      staffPage.locator(`div[role="log"]:has-text("${staffReply}")`)
    ).toBeVisible();

    // -------------------------------------------------------------------------
    // 6. Visitor sees staff reply via visible REST polling
    // -------------------------------------------------------------------------
    await visitorPage.bringToFront();
    await expect(
      visitorPage.locator(`.mcss-message.staff:has-text("${staffReply}")`)
    ).toBeVisible({ timeout: 10_000 });

    // Confirm sender label is present
    await expect(visitorPage.locator("text=Support Agent")).toBeVisible();

    // -------------------------------------------------------------------------
    // 7. Staff closes conversation
    // -------------------------------------------------------------------------
    staffPage.on("dialog", (dialog) => dialog.accept());
    const closeBtn = staffPage.locator("button:has-text('Close conversation')");
    await closeBtn.click();

    // Staff sees closed notice and disabled composer
    await expect(
      staffPage.locator("text=This conversation is closed")
    ).toBeVisible({ timeout: 5_000 });
    await expect(replyComposer).toHaveCount(0);

    // -------------------------------------------------------------------------
    // 8. Visitor side confirms closed status and cannot send further messages
    // -------------------------------------------------------------------------
    await visitorPage.bringToFront();
    await expect(
      visitorPage.locator("text=This conversation is closed")
    ).toBeVisible({ timeout: 10_000 });
    await expect(visitorPage.locator(".mcss-input")).toHaveCount(0);

    // -------------------------------------------------------------------------
    // 9. Security assertions: no console errors, no token leaks
    // -------------------------------------------------------------------------
    expect(visitorErrors).toHaveLength(0);
    // Ignore expected 401 probe during initial unauthenticated login screen load
    const unexpectedStaffErrors = staffErrors.filter(
      (err) => !err.includes("status of 401 (Unauthorized)")
    );
    expect(unexpectedStaffErrors).toHaveLength(0);

    expect(visitorPage.url()).not.toContain("token=");
    expect(staffPage.url()).not.toContain("token=");

    // Clean up
    await visitorContext.close();
    await staffContext.close();
  });
});
