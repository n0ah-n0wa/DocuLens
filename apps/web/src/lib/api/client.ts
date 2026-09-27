import type { ApiErrorBody } from "@doculens/shared-types";

import { appConfig } from "@/lib/config";
import { ApiError, isApiErrorResponse } from "@/lib/api/errors";
import {
  clearSessionTokens,
  getAccessToken,
  getRefreshToken,
  setAccessToken,
  setRefreshToken,
} from "@/lib/auth/session";

export type HttpMethod = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

export interface RequestOptions {
  method?: HttpMethod;
  /**
   * JSON-serialisable body, or `FormData` for multipart uploads.
   * `FormData` must not set Content-Type — the browser supplies the boundary.
   */
  body?: unknown;
  /** When false, the Authorization header is omitted (auth endpoints). */
  auth?: boolean;
  /** Skip the single automatic refresh+retry on 401. */
  skipRefresh?: boolean;
  signal?: AbortSignal;
}

type TokenPairLike = {
  access_token: string;
  refresh_token: string;
};

let refreshInFlight: Promise<boolean> | null = null;

function newRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function fallbackError(status: number, requestId: string, message: string): ApiErrorBody {
  return {
    code: status === 0 ? "NETWORK_ERROR" : "HTTP_ERROR",
    message,
    request_id: requestId,
  };
}

function isTokenPair(value: unknown): value is TokenPairLike {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.access_token === "string" &&
    record.access_token.length > 0 &&
    typeof record.refresh_token === "string" &&
    record.refresh_token.length > 0
  );
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) {
    return null;
  }
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

async function refreshAccessToken(): Promise<boolean> {
  if (refreshInFlight) {
    return refreshInFlight;
  }
  refreshInFlight = (async () => {
    const refreshToken = getRefreshToken();
    if (!refreshToken) {
      return false;
    }
    const requestId = newRequestId();
    try {
      const response = await fetch(`${appConfig.apiBaseUrl}/api/v1/auth/refresh`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          [appConfig.requestIdHeader]: requestId,
        },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      const payload = await parseBody(response);
      if (!response.ok || !isTokenPair(payload)) {
        clearSessionTokens();
        return false;
      }
      setAccessToken(payload.access_token);
      setRefreshToken(payload.refresh_token);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

function assertApiPath(path: string): void {
  if (!path.startsWith("/")) {
    throw new Error(`API path must be absolute within the host (got "${path}").`);
  }
}

/**
 * Central HTTP client for the DocuLens API. All feature modules should call this
 * rather than using `fetch` directly so auth, correlation IDs and error parsing stay consistent.
 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  assertApiPath(path);
  const method = options.method ?? "GET";
  const useAuth = options.auth !== false;
  const requestId = newRequestId();
  const isMultipart = typeof FormData !== "undefined" && options.body instanceof FormData;
  const headers: Record<string, string> = {
    Accept: "application/json",
    [appConfig.requestIdHeader]: requestId,
  };
  if (options.body !== undefined && !isMultipart) {
    headers["Content-Type"] = "application/json";
  }
  if (useAuth) {
    const token = getAccessToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
  }

  const init: RequestInit = { method, headers };
  if (options.body !== undefined) {
    init.body = isMultipart ? (options.body as FormData) : JSON.stringify(options.body);
  }
  if (options.signal !== undefined) {
    init.signal = options.signal;
  }

  let response: Response;
  try {
    response = await fetch(`${appConfig.apiBaseUrl}${path}`, init);
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") {
      throw cause;
    }
    if (cause instanceof Error && cause.name === "AbortError") {
      throw cause;
    }
    throw new ApiError(0, fallbackError(0, requestId, "Unable to reach the DocuLens API."));
  }

  if (response.status === 401 && useAuth) {
    if (options.skipRefresh !== true && getRefreshToken() !== null) {
      const refreshed = await refreshAccessToken();
      if (refreshed) {
        return apiRequest<T>(path, { ...options, skipRefresh: true });
      }
    } else {
      clearSessionTokens();
    }
  }

  const payload = await parseBody(response);
  if (!response.ok) {
    if (isApiErrorResponse(payload)) {
      throw new ApiError(response.status, payload.error);
    }
    throw new ApiError(
      response.status,
      fallbackError(response.status, requestId, `Request failed with status ${response.status}.`),
    );
  }
  return payload as T;
}

/** Attempt a single refresh of the access token. Used by streaming clients that cannot reuse apiRequest's body parsing. */
export async function tryRefreshSession(): Promise<boolean> {
  return refreshAccessToken();
}

export function storeTokenPair(pair: unknown): TokenPairLike {
  if (!isTokenPair(pair)) {
    clearSessionTokens();
    throw new ApiError(500, {
      code: "INVALID_TOKEN_RESPONSE",
      message: "The authentication response was incomplete.",
      request_id: "client",
    });
  }
  setAccessToken(pair.access_token);
  setRefreshToken(pair.refresh_token);
  return pair;
}
