import { describe, expect, it } from "vitest";

import {
  canReindex,
  canReprocess,
  processingStatusLabel,
  shouldPollProcessingStatus,
} from "@/lib/documents/status";
import { validatePdfUpload } from "@/lib/validation/documents";

describe("shouldPollProcessingStatus", () => {
  it("polls pipeline and deleting states only", () => {
    expect(shouldPollProcessingStatus("EMBEDDING")).toBe(true);
    expect(shouldPollProcessingStatus("DELETING")).toBe(true);
    expect(shouldPollProcessingStatus("READY")).toBe(false);
    expect(shouldPollProcessingStatus("FAILED")).toBe(false);
  });
});

describe("document actions", () => {
  it("allows reprocess and reindex only when appropriate", () => {
    expect(canReprocess("FAILED")).toBe(true);
    expect(canReprocess("INDEXING")).toBe(false);
    expect(canReindex("READY")).toBe(true);
    expect(canReindex("UPLOADED")).toBe(false);
  });

  it("labels statuses for display", () => {
    expect(processingStatusLabel("READY")).toBe("Ready");
  });
});

describe("validatePdfUpload", () => {
  it("rejects missing and non-pdf files", () => {
    expect(validatePdfUpload(null).ok).toBe(false);
    const text = new File(["hello"], "notes.txt", { type: "text/plain" });
    expect(validatePdfUpload(text)).toEqual({
      ok: false,
      message: "Only PDF files are supported.",
    });
  });

  it("accepts a non-empty pdf under the size cap", () => {
    const pdf = new File(["%PDF-1.4 content"], "report.pdf", { type: "application/pdf" });
    const result = validatePdfUpload(pdf);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.file.name).toBe("report.pdf");
    }
  });

  it("rejects empty pdfs", () => {
    const empty = new File([], "empty.pdf", { type: "application/pdf" });
    expect(validatePdfUpload(empty)).toEqual({
      ok: false,
      message: "The selected file is empty.",
    });
  });
});
