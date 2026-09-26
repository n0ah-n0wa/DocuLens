/**
 * Error envelope returned by every failing API response (SPECIFICATIONS.md §35).
 *
 * Mirrors `doculens_api.errors.ErrorResponse`. Hand-maintained until contracts are generated from
 * the API's OpenAPI schema; the generated types will replace this file rather than sit next to it.
 */
export interface ApiErrorDetail {
  /** Dotted path to the offending field, e.g. `query.limit`. */
  location: string;
  message: string;
  type: string;
}

export interface ApiErrorBody {
  /** Stable, machine-readable error code, e.g. `DOCUMENT_NOT_READY`. */
  code: string;
  /** Safe, user-facing message. Never contains internal exception details. */
  message: string;
  /** Correlation identifier echoed from the request-ID response header (§36). */
  request_id: string;
  /** Present for request validation failures and selected domain conflicts (e.g. duplicate upload). */
  details?: ApiErrorDetail[];
}

export interface ApiErrorResponse {
  error: ApiErrorBody;
}
