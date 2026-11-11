import { test, expect } from "@playwright/test";

test.describe("Sub-phase 2.3: Admin Auth and Shell", () => {
  test("unauthenticated deep link redirects to /login", async ({ page }) => {
    await page.goto("/inbox");
    await expect(page).toHaveURL(/\/login/);
    await expect(page).toHaveTitle(/Sign in/);

    const emailInput = page.locator("#email");
    const passwordInput = page.locator("#password");
    const submitBtn = page.locator("button[type='submit']");

    await expect(emailInput).toBeVisible();
    await expect(passwordInput).toBeVisible();
    await expect(submitBtn).toBeVisible();
  });

  test("invalid credentials shows safe error alert without leaking server details", async ({ page }) => {
    await page.goto("/login");

    await page.fill("#email", "invalid-user@example.com");
    await page.fill("#password", "WrongPassword123!");
    await page.click("button[type='submit']");

    const alert = page.locator('[role="alert"]');
    await expect(alert).toBeVisible();
    await expect(alert).toContainText("Invalid email or password");
  });

  test("successful login redirects to saved return route", async ({ page }) => {
    // Attempt deep link to knowledge base
    await page.goto("/knowledge");
    await expect(page).toHaveURL(/\/login/);

    await page.fill("#email", "admin@example.com");
    await page.fill("#password", "Asdf@1234");
    await page.click("button[type='submit']");

    // After login, redirected back to /knowledge
    await expect(page).toHaveURL(/\/knowledge/);
    await expect(page.locator("header[role='banner']")).toBeVisible();
    await expect(page.locator("nav[aria-label='Main navigation']")).toBeVisible();
  });

  test("admin vs staff navigation and route protection matrix", async ({ page }) => {
    // 1. Staff login
    await page.goto("/login");
    await page.fill("#email", "staff@example.com");
    await page.fill("#password", "StaffPass123!");
    await page.click("button[type='submit']");

    await expect(page).toHaveURL(/\/inbox/);
    await expect(page.getByText("staff", { exact: true })).toBeVisible();

    // Staff navigation should not contain Settings or Audit Log
    const nav = page.locator("nav[aria-label='Main navigation']");
    await expect(nav.locator("text=Inbox")).toBeVisible();
    await expect(nav.locator("text=Knowledge Base")).toBeVisible();
    await expect(nav.locator("text=Settings")).toHaveCount(0);
    await expect(nav.locator("text=Audit Log")).toHaveCount(0);

    // Direct deep link to admin route redirects staff to /inbox
    await page.goto("/settings/widget");
    await expect(page).toHaveURL(/\/inbox/);

    await page.goto("/audit");
    await expect(page).toHaveURL(/\/inbox/);

    // Logout staff
    await page.click("text=Sign out");
    await expect(page).toHaveURL(/\/login/);

    // 2. Admin login
    await page.fill("#email", "admin@example.com");
    await page.fill("#password", "Asdf@1234");
    await page.click("button[type='submit']");

    await expect(page).toHaveURL(/\/inbox/);
    await expect(page.getByText("admin", { exact: true })).toBeVisible();

    // Admin navigation must contain Settings and Audit Log
    await expect(nav.locator("text=Settings")).toBeVisible();
    await expect(nav.locator("text=Audit Log")).toBeVisible();

    // Admin can visit settings directly
    await page.goto("/settings/widget");
    await expect(page).toHaveURL(/\/settings\/widget/);
  });

  test("logout clears session and prevents back-navigation", async ({ page }) => {
    await page.goto("/login");
    await page.fill("#email", "admin@example.com");
    await page.fill("#password", "Asdf@1234");
    await page.click("button[type='submit']");
    await expect(page).toHaveURL(/\/inbox/);

    // Click Sign out
    await page.click("text=Sign out");
    await expect(page).toHaveURL(/\/login/);

    // Attempting to visit /inbox again redirects to /login
    await page.goto("/inbox");
    await expect(page).toHaveURL(/\/login/);
  });

  test("admin can view and update widget settings and inspect security audit journal", async ({ page }) => {
    await page.goto("/login");
    await page.fill("#email", "admin@example.com");
    await page.fill("#password", "Asdf@1234");
    await page.click("button[type='submit']");
    await expect(page).toHaveURL(/\/inbox/);

    // 1. View and update widget settings
    await page.goto("/settings/widget");
    await expect(page).toHaveURL(/\/settings\/widget/);
    await expect(page.locator("h1")).toContainText("Settings");
    await expect(page.locator("#allowedOrigin")).toBeVisible();
    await expect(page.locator("#welcomeMessage")).toBeVisible();
    await expect(page.locator("#accentColor")).toBeVisible();

    await page.fill("#welcomeMessage", "Updated MCSS Test Support Message");
    await page.click("button:has-text('Save Settings')");
    await expect(page.locator("text=Settings updated successfully")).toBeVisible();

    // 2. View audit log journal
    await page.goto("/audit");
    await expect(page).toHaveURL(/\/audit/);
    await expect(page.locator("h1")).toContainText("Audit Log");
    await expect(page.locator("table")).toBeVisible();
    await expect(page.locator("th:has-text('Event Type')")).toBeVisible();
  });
});
