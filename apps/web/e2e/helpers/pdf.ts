import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

/** Repo root from `apps/web` (Playwright cwd). */
export function repoRoot(): string {
  return join(process.cwd(), "..", "..");
}

/**
 * Build an isolated PDF with known text for grounded retrieval.
 * Uses the shared `doculens.testing.pdfs` helper so content stays predictable.
 */
export function buildHandbookPdf(
  pages: string[] = [
    "Annual leave is twenty-five days per year.",
    "Parental leave is sixteen weeks at full pay.",
  ],
  filename = "handbook.pdf",
): string {
  const dir = mkdtempSync(join(tmpdir(), "doculens-e2e-"));
  const pdfPath = join(dir, filename);
  const script = `
from pathlib import Path
from doculens.testing.pdfs import pdf_with_pages
path = Path(${JSON.stringify(pdfPath)})
path.write_bytes(pdf_with_pages(${JSON.stringify(pages)}))
print(path)
`;
  execFileSync("uv", ["run", "python", "-c", script], {
    cwd: repoRoot(),
    encoding: "utf8",
  });
  return pdfPath;
}

export function cleanupHandbookPdf(pdfPath: string | undefined): void {
  if (!pdfPath) {
    return;
  }
  try {
    rmSync(dirname(pdfPath), { recursive: true, force: true });
  } catch {
    // Best-effort cleanup of the temp directory.
  }
}
