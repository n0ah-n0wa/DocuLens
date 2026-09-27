import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, isApiErrorResponse, messageForApiError } from "@/lib/api/errors";
import { apiRequest } from "@/lib/api/client";
import {
  clearSessionTokens,
  getAccessToken,
  getRefreshToken,
  onSessionCleared,
  setAccessToken,
  setRefreshToken,
} from "@/lib/auth/session";

describe("isApiErrorResponse", () => {
  it("accepts the §35 envelope", () => {
    expect(
      isApiErrorResponse({
        error: {
          code: "VALIDATION_ERROR",
          message: "Invalid email.",
          request_id: "req-1",
        },
      }),
    ).toBe(true);
  });

  it("rejects malformed payloads", () => {
    expect(isApiErrorResponse({ message: "nope" })).toBe(false);
    expect(isApiErrorResponse(null)).toBe(false);
  });
});

describe("messageForApiError", () => {
  it("prefers ApiError messages", () => {
    const error = new ApiError(400, {
      code: "VALIDATION_ERROR",
      message: "Invalid email.",
      request_id: "req-1",
    });
    expect(messageForApiError(error)).toBe("Invalid email.");
  });
});

describe("apiRequest", () => {
  afterEach(() => {
    clearSessionTokens();
    vi.unstubAllGlobals();
  });

  it("attaches the bearer token and parses JSON", async () => {
    setAccessToken("access-token");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "user-1", email: "a@example.com" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await apiRequest<{ id: string; email: string }>("/api/v1/users/me");

    expect(result).toEqual({ id: "user-1", email: "a@example.com" });
    expect(fetchMock).toHaveBeenCalledOnce();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer access-token");
    expect(headers["X-Request-ID"]).toBeTruthy();
  });

  it("throws ApiError for §35 failure bodies", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "INVALID_CREDENTIALS",
            message: "The email address or password is incorrect.",
            request_id: "req-9",
          },
        }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      apiRequest("/api/v1/auth/login", {
        method: "POST",
        body: { email: "a@example.com", password: "wrong-password" },
        auth: false,
      }),
    ).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      code: "INVALID_CREDENTIALS",
      requestId: "req-9",
    });
  });

  it("refreshes once on 401 and retries the original request", async () => {
    setAccessToken("stale");
    setRefreshToken("refresh-1");

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: { code: "UNAUTHORIZED", message: "Expired.", request_id: "r1" },
          }),
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            access_token: "fresh-access",
            refresh_token: "fresh-refresh",
            token_type: "Bearer",
            expires_in: 900,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const result = await apiRequest<{ ok: boolean }>("/api/v1/users/me");

    expect(result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const thirdInit = fetchMock.mock.calls[2]?.[1] as RequestInit;
    const headers = thirdInit.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer fresh-access");
  });

  it("clears the session and notifies listeners when a 401 cannot be refreshed", async () => {
    setAccessToken("stale");
    const cleared = vi.fn();
    const unsubscribe = onSessionCleared(cleared);

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: "UNAUTHORIZED", message: "Expired.", request_id: "r2" },
        }),
        { status: 401 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest("/api/v1/users/me")).rejects.toMatchObject({
      status: 401,
      code: "UNAUTHORIZED",
    });
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
    expect(cleared).toHaveBeenCalledOnce();
    unsubscribe();
  });

  it("rethrows abort errors without wrapping them as ApiError", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new DOMException("Aborted", "AbortError"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest("/api/v1/users/me", { auth: false })).rejects.toMatchObject({
      name: "AbortError",
    });
  });
});
