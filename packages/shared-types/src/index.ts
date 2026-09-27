/**
 * Typed API contracts shared by the web app (SPECIFICATIONS.md §33–§35).
 *
 * Hand-maintained to match the DocuLens OpenAPI document until generation is wired.
 * Answer text, citation quotes and document metadata remain untrusted and must be
 * rendered as text, never as HTML (§53).
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
  /** Present for validation failures and selected domain conflicts (e.g. duplicate upload). */
  details?: ApiErrorDetail[];
}

export interface ApiErrorResponse {
  error: ApiErrorBody;
}

export type UserStatus = "ACTIVE" | "SUSPENDED" | "DELETED";

export interface User {
  id: string;
  email: string;
  status: UserStatus;
  created_at: string;
  last_login_at: string | null;
}

export interface CredentialsRequest {
  email: string;
  password: string;
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "Bearer";
  expires_in: number;
}
