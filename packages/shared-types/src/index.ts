/**
 * Error envelope returned by every failing API response (SPECIFICATIONS.md §35).
 *
 * Hand-maintained until contracts are generated from the API's OpenAPI schema; the generated
 * types will replace this file rather than sit next to it.
 */
export interface ApiErrorBody {
  /** Stable, machine-readable error code, e.g. `DOCUMENT_NOT_READY`. */
  code: string;
  /** Safe, user-facing message. Never contains internal exception details. */
  message: string;
  /** Correlation identifier echoed from the `X-Request-ID` response header (§36). */
  request_id: string;
}

export interface ApiErrorResponse {
  error: ApiErrorBody;
}
