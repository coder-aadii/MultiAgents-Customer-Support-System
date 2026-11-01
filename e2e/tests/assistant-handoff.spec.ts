import { test, expect } from "@playwright/test";

test.describe("Sub-phase 4.5: Controlled Assistant Handoff E2E Flow", () => {
  test("published FAQ gives cited answer → refund gives handoff → staff claim/reply reaches widget → provider count stays zero after claim", async ({
    browser,
  }) => {
    const timestamp = Date.now();
    const docTitle = `Enterprise Operating Hours ${timestamp}`;
    const docContent = `Enterprise support operations run 24 hours a day, 7 days a week for critical incidents.`;
    const visitorName = `EvalVisitor_${timestamp}`;

    const adminContext = await browser.newContext();
    const visitorContext = await browser.newContext();
    const staffContext = await browser.newContext();

    const adminPage = await adminContext.newPage();
    const visitorPage = await visitorContext.newPage();
    const staffPage = await staffContext.newPage();

    // -------------------------------------------------------------------------
    // 1. Admin logs in and publishes FAQ Knowledge Document
    // -------------------------------------------------------------------------
    await adminPage.goto("http://admin-web:5173/login");
    await adminPage.fill("#email", "admin@example.com");
    await adminPage.fill("#password", "Asdf@1234");
    await adminPage.click("button[type='submit']");
    await expect(adminPage).toHaveURL(/\/inbox/);

    await adminPage.click("text=Knowledge Base");
    await expect(adminPage).toHaveURL(/\/knowledge/);

    await adminPage.click("text=+ Add Document");
    const createModal = adminPage.locator("role=dialog");
    await expect(createModal).toBeVisible();

    await createModal.getByRole("button", { exact: true, name: "Write Text" }).click();
    await adminPage.fill("#create-title", docTitle);
    await adminPage.fill("#create-content", docContent);
    await createModal.getByRole("button", { exact: true, name: "Create" }).click();

    await expect(adminPage.locator("role=dialog")).toHaveCount(0);
    const docRow = adminPage.locator("tr", { hasText: docTitle });
    await expect(docRow).toBeVisible();
    await expect(docRow.getByText("Draft", { exact: true })).toBeVisible();

    // Publish Document
    await docRow.getByRole("button", { exact: true, name: "Publish" }).click();
    const publishModal = adminPage.locator("role=dialog");
    await expect(publishModal).toBeVisible();
    await publishModal.getByRole("button", { exact: true, name: "Publish" }).click();
    await expect(adminPage.locator("role=dialog")).toHaveCount(0);
    await expect(docRow.getByText("Published", { exact: true })).toBeVisible();

    // -------------------------------------------------------------------------
    // 2. Visitor opens widget, sends FAQ query -> receives cited answer with source chips
    // -------------------------------------------------------------------------
    await visitorPage.goto("http://widget:5174");
    const launcher = visitorPage.locator(".mcss-launcher-btn");
    await expect(launcher).toBeVisible();
    await launcher.click();

    // Identify visitor
    await visitorPage.locator(".mcss-contact-toggle").click();
    await visitorPage.locator('input[type="text"].mcss-contact-input').fill(visitorName);
    await visitorPage.locator(".mcss-contact-save-btn").click();
    await expect(visitorPage.locator("text=Details saved")).toBeVisible();

    // Send exact FAQ query matching published document
    const visitorInput = visitorPage.locator(".mcss-input");
    await visitorInput.fill(docContent);
    await visitorPage.locator(".mcss-send-btn").click();

    // Verify visitor sees their message
    await expect(
      visitorPage.locator(`.mcss-message.visitor:has-text("${docContent}")`)
    ).toBeVisible();

    // Verify assistant responds with cited answer and source chip
    const sourceChip = visitorPage.locator(".mcss-source-chip");
    await expect(sourceChip.first()).toBeVisible({ timeout: 10_000 });

    // -------------------------------------------------------------------------
    // 3. Visitor asks for a refund -> assistant responds with safe handoff
    // -------------------------------------------------------------------------
    const refundQuery = "I need an immediate refund for order #54321";
    await visitorInput.fill(refundQuery);
    await visitorPage.locator(".mcss-send-btn").click();

    // Verify assistant returns safe handoff message
    const handoffText = "I’m connecting you with our support team so they can help further.";
    await expect(
      visitorPage.locator(`.mcss-message.assistant:has-text("${handoffText}")`)
    ).toBeVisible({ timeout: 10_000 });

    // -------------------------------------------------------------------------
    // 4. Staff logs in to Admin Inbox, views Waiting for Staff queue, and Takes Over
    // -------------------------------------------------------------------------
    await staffPage.goto("http://admin-web:5173/login");
    await staffPage.fill("#email", "staff@example.com");
    await staffPage.fill("#password", "StaffPass123!");
    await staffPage.click("button[type='submit']");
    await expect(staffPage).toHaveURL(/\/inbox/);

    // Filter by "Waiting for staff"
    await staffPage.click("button:has-text('Waiting for staff')");

    const convItem = staffPage.locator(`[data-testid="conversation-item"]:has-text("${visitorName}")`);
    await expect(convItem).toBeVisible({ timeout: 10_000 });
    await convItem.click();

    // Staff sees refund message and handoff notice
    await expect(staffPage.locator(`div[role="log"]:has-text("${refundQuery}")`)).toBeVisible();

    // Staff clicks Take over
    const takeOverBtn = staffPage.locator("button:has-text('Take over')");
    await expect(takeOverBtn).toBeVisible();
    await takeOverBtn.click();

    // Status updates to Staff active
    await expect(staffPage.locator("text=Staff active").first()).toBeVisible({ timeout: 5_000 });

    // -------------------------------------------------------------------------
    // 5. Staff replies to visitor
    // -------------------------------------------------------------------------
    const staffReply = `Hello ${visitorName}, I am taking over this ticket to process your refund.`;
    const replyComposer = staffPage.locator("#reply-composer");
    await replyComposer.fill(staffReply);
    await staffPage.locator("button[type='submit']:has-text('Send')").click();

    // Staff sees reply in their timeline
    await expect(staffPage.locator(`div[role="log"]:has-text("${staffReply}")`)).toBeVisible();

    // -------------------------------------------------------------------------
    // 6. Visitor sees staff reply via REST polling
    // -------------------------------------------------------------------------
    await visitorPage.bringToFront();
    await expect(
      visitorPage.locator(`.mcss-message.staff:has-text("${staffReply}")`)
    ).toBeVisible({ timeout: 10_000 });
    await expect(visitorPage.locator("text=Support Agent")).toBeVisible();

    // -------------------------------------------------------------------------
    // 7. Verify provider calls stay zero after claim
    // -------------------------------------------------------------------------
    const initialAssistantMsgCount = await visitorPage.locator(".mcss-message.assistant").count();

    // Visitor sends another message while staff_active
    const postClaimQuery = "Thank you, what is the next step?";
    await visitorInput.fill(postClaimQuery);
    await visitorPage.locator(".mcss-send-btn").click();

    await expect(
      visitorPage.locator(`.mcss-message.visitor:has-text("${postClaimQuery}")`)
    ).toBeVisible();

    // Wait 5 seconds to ensure polling completes and NO new AI assistant message is produced
    await visitorPage.waitForTimeout(5000);
    const postAssistantMsgCount = await visitorPage.locator(".mcss-message.assistant").count();
    expect(postAssistantMsgCount).toBe(initialAssistantMsgCount);

    // Clean up
    await adminContext.close();
    await visitorContext.close();
    await staffContext.close();
  });
});

