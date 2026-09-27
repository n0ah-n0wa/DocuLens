import type { ApiErrorBody, ApiErrorResponse } from "@doculens/shared-types";

/**
 * Thrown for every non-2xx API response that carries the §35 error envelope,
 * and for transport failures when no envelope is available.
 * AbortError is never wrapped — callers that cancel requests should catch it directly.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string;
  readonly details: ApiErrorBody["details"];

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.requestId = body.request_id;
    this.details = body.details;
  }
}

export function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const error = (value as ApiErrorResponse).error;
  return (
    typeof error === "object" &&
    error !== null &&
    typeof error.code === "string" &&
    typeof error.message === "string" &&
    typeof error.request_id === "string"
  );
}

export interface DescribedApiError {
  message: string;
  code?: string;
  requestId?: string;
  /** Present for DUPLICATE_DOCUMENT when the API returns the existing id. */
  existingDocumentId?: string;
}

function detailMessage(details: ApiErrorBody["details"], location: string): string | undefined {
  return details?.find((detail) => detail.location === location)?.message;
}

/** Structured presentation helper for forms and alerts. */
export function describeApiError(
  error: unknown,
  fallback = "Something went wrong.",
): DescribedApiError {
  if (error instanceof ApiError) {
    const existingDocumentId = detailMessage(error.details, "existing_document_id");
    let message = error.message;

    if (error.code === "DUPLICATE_DOCUMENT") {
      message = existingDocumentId
        ? `${error.message} Open the existing document to continue.`
        : error.message;
    } else if (error.status === 429 || error.code === "RATE_LIMITED") {
      message = error.message || "Too many requests. Wait a moment and try again.";
    } else if (error.status === 413 || error.code === "FILE_TOO_LARGE") {
      message = error.message || "The file is too large to upload.";
    } else if (error.code === "UNSUPPORTED_FILE_TYPE" || error.code === "INVALID_FILE_SIGNATURE") {
      message = error.message || "Only valid PDF files can be uploaded.";
    } else if (error.code === "EMPTY_FILE") {
      message = error.message || "The selected file is empty.";
    }

    return {
      message,
      code: error.code,
      requestId: error.requestId,
      ...(existingDocumentId !== undefined ? { existingDocumentId } : {}),
    };
  }
  if (error instanceof Error && error.message) {
    return { message: error.message };
  }
  return { message: fallback };
}

export function messageForApiError(error: unknown, fallback = "Something went wrong."): string {
  return describeApiError(error, fallback).message;
}
