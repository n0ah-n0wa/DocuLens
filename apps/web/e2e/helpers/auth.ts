import { expect, type Page } from "@playwright/test";

/** Unique credentials per run so parallel CI jobs and retries never collide. */
export function uniqueCredentials(prefix = "e2e"): { email: string; password: string } {
  const stamp = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  return {
    email: `${prefix}-${stamp}@example.com`,
    password: "correct horse battery staple",
  };
}

export async function registerAccount(
  page: Page,
  credentials: { email: string; password: string },
): Promise<void> {
  await page.goto("/register");
  await expect(page.getByRole("heading", { level: 1, name: "Create an account" })).toBeVisible();
  await page.getByLabel("Email").fill(credentials.email);
  await page.getByLabel("Password").fill(credentials.password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 30_000 });
  // Email is visually hidden on narrow viewports (`hidden md:block`) but stays in the DOM.
  await expect(page.getByTestId("signed-in-email")).toHaveText(credentials.email);
}

export async function login(
  page: Page,
  credentials: { email: string; password: string },
): Promise<void> {
  await page.goto("/login");
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill(credentials.email);
  await page.getByLabel("Password").fill(credentials.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 30_000 });
  await expect(page.getByTestId("signed-in-email")).toHaveText(credentials.email);
}

export async function logout(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/, { timeout: 30_000 });
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
  await expect(page.getByTestId("signed-in-email")).toHaveCount(0);
}
