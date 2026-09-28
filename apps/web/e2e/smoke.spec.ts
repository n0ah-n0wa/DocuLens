import { expect, test } from "@playwright/test";

test("landing page renders the product heading and auth links", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle("DocuLens");
  await expect(page.getByRole("heading", { level: 1, name: "DocuLens" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Create account" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
});

test("login page renders the sign-in form", async ({ page }) => {
  await page.goto("/login");

  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeEnabled();
});
