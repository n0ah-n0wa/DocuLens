/**
 * Critical path against a running local stack (API :8000, web :3000, compose infra).
 * Opt-in only (ignored by default CI e2e):
 *   CRITICAL_PATH=1 pnpm --filter @doculens/web exec playwright test e2e/critical-path.spec.ts
 */
import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const email = `checkpoint-${Date.now()}@example.com`;
const password = "correct horse battery staple";

function buildHandbookPdf(): string {
  const dir = mkdtempSync(join(tmpdir(), "doculens-cp-"));
  const pdfPath = join(dir, "handbook.pdf");
  const repoRoot = join(process.cwd(), "..", "..");
  const script = `
from pathlib import Path
from doculens.testing.pdfs import pdf_with_pages
path = Path(${JSON.stringify(pdfPath)})
path.write_bytes(pdf_with_pages([
    "Annual leave is twenty-five days per year.",
    "Parental leave is sixteen weeks at full pay.",
]))
print(path)
`;
  execFileSync("uv", ["run", "python", "-c", script], {
    cwd: repoRoot,
    encoding: "utf8",
  });
  return pdfPath;
}

test.describe.configure({ mode: "serial", timeout: 180_000 });

test("critical path: login → upload → process → ask → citation → follow-up", async ({
  page,
  request,
}) => {
  const pdfPath = buildHandbookPdf();

  // Register (creates account) then land on dashboard via login form after register
  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /create account|register|sign up/i }).click();
  await expect(page).toHaveURL(/\/(dashboard|login)/, { timeout: 30_000 });

  if (page.url().includes("/login")) {
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: /sign in/i }).click();
  }
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 30_000 });

  // Upload
  await page.goto("/documents/upload");
  await page.locator("#upload-file").setInputFiles(pdfPath);
  await page.getByRole("button", { name: /upload document/i }).click();
  await expect(page).toHaveURL(/\/documents\/[0-9a-f-]+/, { timeout: 30_000 });
  const documentUrl = page.url();
  const documentId = documentUrl.split("/").pop()!;

  // Drive processing if the queue did not pick it up (memory queue is per-process)
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const badge = page.getByLabel(/Processing status:/i);
    const label = ((await badge.textContent()) ?? "").trim();
    if (/ready/i.test(label)) {
      break;
    }
    if (/failed/i.test(label)) {
      throw new Error(`Document processing failed: ${label}`);
    }
    if (attempt === 0 || attempt % 5 === 0) {
      try {
        execFileSync("uv", ["run", "python", "-m", "doculens_worker", "process", documentId], {
          cwd: join(process.cwd(), "../.."),
          encoding: "utf8",
          timeout: 120_000,
        });
      } catch {
        // Worker may race with an in-flight transition; keep polling.
      }
    }
    await page.reload();
    await page.waitForTimeout(1500);
  }
  await expect(page.getByLabel(/Processing status:/i)).toContainText(/ready/i, {
    timeout: 10_000,
  });

  // Ask
  await page.goto("/chat");
  await page.getByRole("button", { name: /^new$/i }).click();
  await page.getByLabel("Title").fill("Leave policy");
  await page.getByRole("button", { name: /create conversation/i }).click();
  await expect(page).toHaveURL(/\/chat\/[0-9a-f-]+/, { timeout: 30_000 });

  await page
    .getByRole("textbox", { name: "Question" })
    .fill("How many days of annual leave do employees get?");
  await page.getByRole("button", { name: /^ask$/i }).click();

  await expect(page.getByText(/Fake answer to:|twenty-five|annual leave/i).first()).toBeVisible({
    timeout: 60_000,
  });

  // Inspect citation (panel may be toggled on smaller viewports)
  const citationsToggle = page.getByRole("button", { name: /citations/i });
  if (await citationsToggle.isVisible()) {
    const expanded = await citationsToggle.getAttribute("aria-expanded");
    if (expanded === "false") {
      await citationsToggle.click();
    }
  }
  await expect(page.getByRole("heading", { name: "Citations" })).toBeVisible();
  await expect(page.getByText(/Annual leave is twenty-five days/i).first()).toBeVisible({
    timeout: 15_000,
  });
  const citationChip = page.getByRole("button", { name: /Show citation/i }).first();
  if (await citationChip.isVisible()) {
    await citationChip.click();
  }
  await expect(
    page.getByText(/Source document unavailable|handbook\.pdf|Open document/i).first(),
  ).toBeVisible();

  // Follow-up
  await page.getByRole("textbox", { name: "Question" }).fill("What about parental leave?");
  await page.getByRole("button", { name: /^ask$/i }).click();
  await expect(page.getByText(/parental|sixteen weeks|Fake answer to:/i).first()).toBeVisible({
    timeout: 60_000,
  });

  // Sanity: API still healthy
  const health = await request.get("http://127.0.0.1:8000/health/live");
  expect(health.ok()).toBeTruthy();
});
