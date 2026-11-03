import { test, expect } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

test.describe("Stage 1: Multi-file upload in Knowledge Base Admin UI", () => {
  const tmpDir = "/tmp/mcss-multi-upload-test";

  test.beforeAll(() => {
    if (!fs.existsSync(tmpDir)) {
      fs.mkdirSync(tmpDir, { recursive: true });
    }
  });

  test("multi-file selection, title derivation, editable title, concurrent upload and progress", async ({
    page,
  }) => {
    // Prepare 3 test markdown/txt files
    const file1 = path.join(tmpDir, "multi-alpha.md");
    const file2 = path.join(tmpDir, "multi-beta.md");
    const file3 = path.join(tmpDir, "multi-gamma.txt");

    fs.writeFileSync(
      file1,
      "# Multi Alpha Policy\nAlpha policy details for multi-upload verification testing."
    );
    fs.writeFileSync(
      file2,
      "# Multi Beta Policy\nBeta policy details for multi-upload verification testing."
    );
    fs.writeFileSync(
      file3,
      "Multi Gamma plain text content for multi-upload verification testing."
    );

    // 1. Admin login
    await page.goto("/login");
    await page.fill("#email", "admin@example.com");
    await page.fill("#password", "Asdf@1234");
    await page.click("button[type='submit']");
    await expect(page).toHaveURL(/\/inbox/);

    // 2. Navigate to Knowledge Base
    await page.click("text=Knowledge Base");
    await expect(page).toHaveURL(/\/knowledge/);

    // 3. Open Add Document modal
    await page.click("text=+ Add Document");
    const modal = page.locator("role=dialog");
    await expect(modal).toBeVisible();

    // 4. Select multiple files
    const fileInput = modal.locator("#file-upload");
    await fileInput.setInputFiles([file1, file2, file3]);

    // 5. Verify queue items appear with auto-derived titles
    await expect(modal.getByText("Selected Files (3)")).toBeVisible();
    await expect(modal.getByText("multi-alpha.md")).toBeVisible();
    await expect(modal.getByText("multi-beta.md")).toBeVisible();
    await expect(modal.getByText("multi-gamma.txt")).toBeVisible();

    // 6. Verify titles are editable: edit title of first item
    const titleInputs = modal.locator("input[placeholder='Document Title']");
    await expect(titleInputs).toHaveCount(3);
    await titleInputs.nth(0).fill("Custom Alpha Multi-Test");

    // 7. Verify status badges are initially 'Queued'
    await expect(modal.getByText("Queued")).toHaveCount(3);

    // 8. Start Upload
    const uploadBtn = modal.getByRole("button", { name: "Upload 3 Files" });
    await expect(uploadBtn).toBeVisible();
    await uploadBtn.click();

    // 9. Wait for all 3 files to succeed
    await expect(modal.getByText("✓ Succeeded")).toHaveCount(3, { timeout: 30000 });
    await expect(modal.getByText("3 / 3 files processed")).toBeVisible();

    // 10. Click Done to close modal
    await modal.getByRole("button", { name: "Done" }).click();
    await expect(page.locator("role=dialog")).toHaveCount(0);

    // 11. Verify documents appear in the table
    await expect(page.locator("tr", { hasText: "Custom Alpha Multi-Test" })).toBeVisible();
    await expect(page.locator("tr", { hasText: "multi-beta" })).toBeVisible();
    await expect(page.locator("tr", { hasText: "multi-gamma" })).toBeVisible();
  });

  test("queue item removal and oversized file handling", async ({ page }) => {
    const normalFile = path.join(tmpDir, "normal-file.md");
    fs.writeFileSync(normalFile, "# Normal File\nValid content.");

    // Admin login
    await page.goto("/login");
    await page.fill("#email", "admin@example.com");
    await page.fill("#password", "Asdf@1234");
    await page.click("button[type='submit']");
    await page.click("text=Knowledge Base");

    // Open modal
    await page.click("text=+ Add Document");
    const modal = page.locator("role=dialog");
    await expect(modal).toBeVisible();

    // Select file
    const fileInput = modal.locator("#file-upload");
    await fileInput.setInputFiles([normalFile]);
    await expect(modal.getByText("Selected Files (1)")).toBeVisible();

    // Remove file from queue
    const removeBtn = modal.locator("button[title='Remove from queue']");
    await removeBtn.click();
    await expect(modal.getByText("Selected Files (1)")).toHaveCount(0);
    await expect(modal.getByText("Drag and drop .txt, .md, or text-based .pdf files here")).toBeVisible();

    // Cancel modal
    await modal.getByRole("button", { name: "Cancel" }).click();
    await expect(page.locator("role=dialog")).toHaveCount(0);
  });
});

