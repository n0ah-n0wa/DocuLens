import { expect, test } from "@playwright/test";

test("landing page renders the product heading", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle("DocuLens");
  await expect(page.getByRole("heading", { level: 1, name: "DocuLens" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
});
