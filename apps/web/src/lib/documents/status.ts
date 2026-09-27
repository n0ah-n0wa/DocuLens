import type { ProcessingStatus } from "@doculens/shared-types";

const STATUS_LABELS: Record<ProcessingStatus, string> = {
  UPLOADED: "Uploaded",
  VALIDATING: "Validating",
  EXTRACTING: "Extracting text",
  CHUNKING: "Chunking",
  EMBEDDING: "Embedding",
  INDEXING: "Indexing",
  READY: "Ready",
  FAILED: "Failed",
  DELETING: "Deleting",
  DELETED: "Deleted",
};

/** Statuses that still need live updates from the API. */
export function shouldPollProcessingStatus(status: ProcessingStatus): boolean {
  return status !== "READY" && status !== "FAILED" && status !== "DELETED";
}

export function canReprocess(status: ProcessingStatus): boolean {
  return status === "READY" || status === "FAILED" || status === "UPLOADED";
}

export function canReindex(status: ProcessingStatus): boolean {
  return status === "READY" || status === "FAILED";
}

export function processingStatusLabel(status: ProcessingStatus): string {
  return STATUS_LABELS[status];
}

export function processingStatusTone(
  status: ProcessingStatus,
): "neutral" | "progress" | "success" | "danger" {
  if (status === "READY") {
    return "success";
  }
  if (status === "FAILED") {
    return "danger";
  }
  if (status === "DELETING" || status === "DELETED") {
    return "neutral";
  }
  return "progress";
}
