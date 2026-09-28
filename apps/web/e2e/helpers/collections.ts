import { expect, type Page } from "@playwright/test";

export async function createCollection(
  page: Page,
  name: string,
  description?: string,
): Promise<string> {
  await page.goto("/collections");
  await expect(page.getByRole("heading", { level: 1, name: "Collections" })).toBeVisible();
  await page.getByLabel("Name", { exact: true }).fill(name);
  if (description) {
    await page.getByLabel("Description (optional)").fill(description);
  }
  await page.getByRole("button", { name: "Create collection" }).click();
  await expect(page).toHaveURL(/\/collections\/[0-9a-f-]{36}/i, { timeout: 30_000 });
  const collectionId = page.url().split("/").pop();
  if (!collectionId) {
    throw new Error("Create collection redirected without an id");
  }
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  return collectionId;
}
