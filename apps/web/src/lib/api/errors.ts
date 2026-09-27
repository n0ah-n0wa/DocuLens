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

export function messageForApiError(error: unknown, fallback = "Something went wrong."): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}
