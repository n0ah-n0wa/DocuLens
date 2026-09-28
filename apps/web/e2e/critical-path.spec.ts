/**
 * Critical end-to-end flows against a running DocuLens stack (API, worker, compose infra).
 *
 * Local / CI:
 *   NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 pnpm run build
 *   pnpm run test:e2e:critical
 *
 * Requires API at NEXT_PUBLIC_API_BASE_URL (default http://127.0.0.1:8000), migrations applied,
 * and fake embedding/LLM providers. Document Ready is driven by the worker consumer or the
 * `doculens_worker process` CLI fallback used by the helpers.
 *
 * Tests share one browser page so the in-memory access token and sessionStorage refresh token
 * survive across steps (DocuLens does not use cookies for auth). Each run uses isolated emails
 * and a fresh PDF; temp PDF files are removed in afterAll.
 */
import { expect, test, type Browser, type Page } from "@playwright/test";

import { login, logout, registerAccount, uniqueCredentials } from "./helpers/auth";
import {
  askQuestion,
  createConversation,
  expectGroundedAnswer,
  inspectFirstCitation,
} from "./helpers/chat";
import { createCollection } from "./helpers/collections";
import {
  deleteCurrentDocument,
  moveDocumentToCollection,
  uploadPdf,
  waitForDocumentReady,
} from "./helpers/documents";
import { buildHandbookPdf, cleanupHandbookPdf } from "./helpers/pdf";

test.describe.configure({ mode: "serial", timeout: 180_000 });

const owner = uniqueCredentials("owner");
const stranger = uniqueCredentials("stranger");
const collectionName = `Policies ${Date.now()}`;

let browser: Browser;
let page: Page;
let pdfPath: string;
let documentId: string;
let documentUrl: string;
let collectionId: string;

test.beforeAll(async ({ browser: testBrowser }) => {
  browser = testBrowser;
  page = await browser.newPage();
  pdfPath = buildHandbookPdf();
});

test.afterAll(async () => {
  await page.close();
  cleanupHandbookPdf(pdfPath);
});

test("registers an isolated account", async () => {
  await registerAccount(page, owner);
});

test("logs out and signs back in", async () => {
  await expect(page.getByTestId("signed-in-email")).toHaveText(owner.email);
  await logout(page);
  await login(page, owner);
});

test("creates a collection", async () => {
  collectionId = await createCollection(page, collectionName, "E2E leave policies");
  expect(collectionId).toMatch(/^[0-9a-f-]{36}$/i);
});

test("uploads a PDF and shows processing status through Ready", async () => {
  documentId = await uploadPdf(page, pdfPath);
  documentUrl = page.url();

  const status = page.getByLabel(/Processing status:/i);
  await expect(status).toBeVisible();
  await expect(status).toContainText(
    /Uploaded|Validating|Extracting|Chunking|Embedding|Indexing|Ready/i,
  );

  await waitForDocumentReady(page, documentId);
  await expect(status).toContainText(/Ready/i);
});

test("moves the document into the collection", async () => {
  await page.goto(documentUrl);
  await expect(page.getByLabel(/Processing status:/i)).toContainText(/Ready/i);
  await moveDocumentToCollection(page, collectionName);
  await expect(page.locator("#document-collection")).toHaveValue(collectionId);
});

test("creates a conversation, asks, receives a grounded answer, and inspects a citation", async () => {
  await createConversation(page, "Leave policy");
  await askQuestion(page, "How many days of annual leave do employees get?");
  await expectGroundedAnswer(page, { assistantTurns: 1 });
  await inspectFirstCitation(page);
});

test("asks a follow-up question in the same conversation", async () => {
  await askQuestion(page, "What about parental leave?");
  await expectGroundedAnswer(page, { assistantTurns: 2 });
});

test("deletes the document", async () => {
  await page.goto(documentUrl);
  await deleteCurrentDocument(page);
  await page.goto(documentUrl);
  await expect(page.getByRole("alert").filter({ hasText: "Document unavailable" })).toBeVisible({
    timeout: 30_000,
  });
});

test("enforces the authorization boundary for another account", async () => {
  await logout(page);
  await registerAccount(page, stranger);

  await page.goto(documentUrl);
  await expect(page.getByRole("alert").filter({ hasText: "Document unavailable" })).toBeVisible({
    timeout: 30_000,
  });

  await page.goto(`/collections/${collectionId}`);
  await expect(page.getByRole("alert").filter({ hasText: "Collection unavailable" })).toBeVisible({
    timeout: 30_000,
  });
});
