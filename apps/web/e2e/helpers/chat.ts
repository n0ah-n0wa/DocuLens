import { expect, type Page } from "@playwright/test";

export async function createConversation(page: Page, title: string): Promise<string> {
  await page.goto("/chat");
  await page.getByRole("button", { name: /^New$/i }).click();
  await page.getByLabel("Title").fill(title);
  await page.getByRole("button", { name: "Create conversation" }).click();
  await expect(page).toHaveURL(/\/chat\/[0-9a-f-]{36}/i, { timeout: 30_000 });
  const conversationId = page.url().split("/").pop();
  if (!conversationId) {
    throw new Error("Create conversation redirected without an id");
  }
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
  return conversationId;
}

/** Submit a question. Pair with expectGroundedAnswer — do not poll for idle Ask alone (racey). */
export async function askQuestion(page: Page, question: string): Promise<void> {
  const composer = page.getByRole("textbox", { name: "Question" });
  const ask = page.getByRole("button", { name: "Ask", exact: true });
  await expect(ask).toBeEnabled();
  await composer.fill(question);
  await ask.click();
}

/**
 * Structural checks for a grounded turn: assistant bubble(s), citation evidence panel,
 * idle composer. Does not match exact model prose.
 */
export async function expectGroundedAnswer(
  page: Page,
  options: { assistantTurns: number },
): Promise<void> {
  const history = page.getByLabel("Message history");
  await expect(history.getByText("Assistant", { exact: true })).toHaveCount(
    options.assistantTurns,
    {
      timeout: 60_000,
    },
  );
  await expect(history.getByText(/\d+ citations? — open in the Citations panel\./i)).toBeVisible({
    timeout: 15_000,
  });
  await openCitationsPanel(page);
  await expect(
    page.getByRole("list", { name: "Citations" }).getByRole("listitem").first(),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeEnabled();
  await expect(page.getByRole("textbox", { name: "Question" })).toHaveValue("");
}

export async function openCitationsPanel(page: Page): Promise<void> {
  const panel = page.locator("#citations-panel");
  const heading = page.getByRole("heading", { name: "Citations" });
  if (!(await panel.isVisible())) {
    const toggle = page.getByRole("button", { name: /Citations/i });
    await expect(toggle).toBeVisible();
    await toggle.click();
  }
  await expect(heading).toBeVisible();
  await expect(panel).toBeVisible();
}

/** Inspect citation evidence structurally: non-empty quote, page number, document identity. */
export async function inspectFirstCitation(page: Page): Promise<void> {
  await openCitationsPanel(page);
  const list = page.getByRole("list", { name: "Citations" });
  const first = list.getByRole("listitem").first();
  await expect(first).toBeVisible({ timeout: 15_000 });

  const quote = first.locator("blockquote");
  await expect(quote).toBeVisible();
  await expect(quote).not.toHaveText(/^\s*$/);
  await expect(first.getByText(/^Page \d+$/)).toBeVisible();
  await expect(first.getByText(/handbook\.pdf|Source document unavailable/i)).toBeVisible();

  const select = first.getByRole("button", { name: /Select source|Selected source/i });
  if (await select.isVisible()) {
    await select.click();
    await expect(first.getByRole("button", { name: "Selected source" })).toBeVisible();
  }
}
