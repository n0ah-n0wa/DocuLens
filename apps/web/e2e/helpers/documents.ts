import { expect, type Page } from "@playwright/test";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

import { repoRoot } from "./pdf";

const execFileAsync = promisify(execFile);

export async function uploadPdf(
  page: Page,
  pdfPath: string,
  options?: { collectionName?: string },
): Promise<string> {
  await page.goto("/documents/upload");
  await expect(page.getByRole("heading", { level: 1, name: "Upload a PDF" })).toBeVisible();
  await page.locator("#upload-file").setInputFiles(pdfPath);
  if (options?.collectionName) {
    await page.getByLabel("Collection (optional)").selectOption({ label: options.collectionName });
  }
  await page.getByRole("button", { name: "Upload document" }).click();
  await expect(page).toHaveURL(/\/documents\/[0-9a-f-]{36}/i, { timeout: 30_000 });
  const documentId = page.url().split("/").pop();
  if (!documentId) {
    throw new Error("Upload redirected without a document id");
  }
  return documentId;
}

/** Drive processing when the queue consumer is absent or lagging (memory-queue / local). */
export async function processDocumentOnce(documentId: string): Promise<void> {
  try {
    await execFileAsync("uv", ["run", "python", "-m", "doculens_worker", "process", documentId], {
      cwd: repoRoot(),
      timeout: 120_000,
    });
  } catch {
    // Concurrent transitions are expected; the Ready poll keeps retrying.
  }
}

/**
 * Wait until the detail badge is Ready. Uses expect().toPass (no fixed sleeps).
 * Periodically kicks the worker CLI so stacks without a live consumer still finish.
 */
export async function waitForDocumentReady(page: Page, documentId: string): Promise<void> {
  const status = page.getByLabel(/Processing status:/i);
  let kicks = 0;

  await expect(async () => {
    await expect(status).toBeVisible();
    const label = ((await status.textContent()) ?? "").trim();
    if (/failed/i.test(label)) {
      throw new Error(`Document processing failed: ${label}`);
    }
    if (/ready/i.test(label)) {
      return;
    }
    if (kicks < 20) {
      kicks += 1;
      await processDocumentOnce(documentId);
    }
    await page.reload();
    await expect(status).toBeVisible();
    const after = ((await status.textContent()) ?? "").trim();
    expect(after, `processing status still "${after}"`).toMatch(/ready/i);
  }).toPass({ timeout: 180_000, intervals: [250, 500, 1_000, 2_000] });
}

export async function moveDocumentToCollection(page: Page, collectionName: string): Promise<void> {
  const collection = page.locator("#document-collection");
  await expect(collection).toBeEnabled();
  await collection.selectOption({ label: collectionName });
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Document updated.")).toBeVisible({ timeout: 15_000 });
}

export async function deleteCurrentDocument(page: Page): Promise<void> {
  await page
    .getByRole("region", { name: "Actions" })
    .getByRole("button", { name: "Delete" })
    .click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByRole("heading", { name: "Delete document?" })).toBeVisible();
  await dialog.getByRole("button", { name: "Delete document" }).click();
  await expect(page).toHaveURL(/\/documents\/?$/, { timeout: 30_000 });
}
