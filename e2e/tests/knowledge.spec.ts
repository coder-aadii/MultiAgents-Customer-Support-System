import { test, expect } from "@playwright/test";

test.describe("Sub-phase 3.5: Knowledge Base Admin UI and Retrieval Testbed", () => {
  const testTitle = `Acme Return Policy ${Date.now()}`;
  const testContent =
    "All unopened and unworn items can be returned within 30 days of purchase for a full refund or exchange. Original receipt required.";

  test("full document lifecycle: draft -> publish -> retrieve -> archive -> exclude", async ({
    page,
  }) => {
    // 1. Admin login
    await page.goto("/login");
    await page.fill("#email", "admin@example.com");
    await page.fill("#password", "Asdf@1234");
    await page.click("button[type='submit']");
    await expect(page).toHaveURL(/\/inbox/);

    // 2. Navigate to Knowledge Base
    await page.click("text=Knowledge Base");
    await expect(page).toHaveURL(/\/knowledge/);
    await expect(page.locator("h1")).toContainText("Knowledge Base");

    // 3. Open Add Document modal
    await page.click("text=+ Add Document");
    const createModal = page.locator("role=dialog");
    await expect(createModal).toBeVisible();

    // Switch to Write Text tab
    await createModal.getByRole("button", { exact: true, name: "Write Text" }).click();
    await page.fill("#create-title", testTitle);
    await page.fill("#create-content", testContent);
    await createModal.getByRole("button", { exact: true, name: "Create" }).click();

    // Modal closes and document appears in list
    await expect(page.locator("role=dialog")).toHaveCount(0);
    const docRow = page.locator("tr", { hasText: testTitle });
    await expect(docRow).toBeVisible();
    await expect(docRow.getByText("Draft", { exact: true })).toBeVisible();
    await expect(docRow.getByText("Ready", { exact: false })).toBeVisible();

    // 4. View Details
    await docRow.getByRole("button", { exact: true, name: "View" }).click();
    const detailModal = page.locator("role=dialog");
    await expect(detailModal).toBeVisible();
    await expect(detailModal).toContainText(testTitle);
    await expect(detailModal).toContainText("Ingested Chunks");
    await detailModal.getByRole("button", { exact: true, name: "Close" }).click();
    await expect(page.locator("role=dialog")).toHaveCount(0);

    // 5. Publish Document
    await docRow.getByRole("button", { exact: true, name: "Publish" }).click();
    const publishModal = page.locator("role=dialog");
    await expect(publishModal).toBeVisible();
    await expect(publishModal).toContainText("Answers may use this document immediately.");
    await publishModal.getByRole("button", { exact: true, name: "Publish" }).click();
    await expect(page.locator("role=dialog")).toHaveCount(0);

    // Status is now Published
    await expect(docRow.getByText("Published", { exact: true })).toBeVisible();

    // 6. Test Retrieval Testbed
    await page.getByRole("button", { exact: true, name: "Retrieval Testbed" }).click();
    await expect(page.locator("#testbed-query")).toBeVisible();

    await page.fill("#testbed-query", testContent);
    await page.getByRole("button", { exact: true, name: "Test Retrieval" }).click();

    // Results appear containing our test title
    const resultsContainer = page.locator("text=Top Ranked Chunks");
    await expect(resultsContainer).toBeVisible({ timeout: 10000 });
    await expect(page.locator(".bg-white", { hasText: testTitle }).first()).toBeVisible();
    await expect(page.locator(".bg-gray-50", { hasText: "unopened and unworn items" }).first()).toBeVisible();

    // 7. Archive Document
    await page.getByRole("button", { exact: true, name: "Documents" }).click();
    await expect(docRow).toBeVisible();
    await docRow.getByRole("button", { exact: true, name: "Archive" }).click();
    const archiveModal = page.locator("role=dialog");
    await expect(archiveModal).toBeVisible();
    await archiveModal.getByRole("button", { exact: true, name: "Archive" }).click();
    await expect(page.locator("role=dialog")).toHaveCount(0);

    // Status is now Archived
    await expect(docRow.getByText("Archived", { exact: true })).toBeVisible();

    // 8. Confirm Exclusion in Retrieval Testbed
    await page.getByRole("button", { exact: true, name: "Retrieval Testbed" }).click();
    await page.getByRole("button", { exact: true, name: "Test Retrieval" }).click();

    // Check diagnostics: excluded unpublished count should be >= 1
    await expect(
      page.getByText("matching chunks belonged to draft or archived documents", { exact: false })
    ).toBeVisible({ timeout: 10000 });
  });

  test("staff user has read-only access without edit/publish/delete controls", async ({
    page,
  }) => {
    // 1. Staff login
    await page.goto("/login");
    await page.fill("#email", "staff@example.com");
    await page.fill("#password", "StaffPass123!");
    await page.click("button[type='submit']");
    await expect(page).toHaveURL(/\/inbox/);

    // 2. Visit Knowledge Base
    await page.goto("/knowledge");
    await expect(page.locator("h1")).toContainText("Knowledge Base");

    // Staff should NOT see "+ Add Document"
    await expect(page.locator("button:has-text('+ Add Document')")).toHaveCount(0);

    // Staff can see Documents list and Retrieval Testbed tab
    await expect(page.getByRole("button", { exact: true, name: "Documents" })).toBeVisible();
    await expect(page.getByRole("button", { exact: true, name: "Retrieval Testbed" })).toBeVisible();

    // Table rows should not have Edit, Publish, Archive, or Delete buttons
    await expect(page.getByRole("button", { exact: true, name: "Edit" })).toHaveCount(0);
    await expect(page.getByRole("button", { exact: true, name: "Publish" })).toHaveCount(0);
    await expect(page.getByRole("button", { exact: true, name: "Archive" })).toHaveCount(0);
    await expect(page.getByRole("button", { exact: true, name: "Delete" })).toHaveCount(0);
  });
});
