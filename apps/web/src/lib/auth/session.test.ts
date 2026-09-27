import { afterEach, describe, expect, it, vi } from "vitest";

import {
  clearSessionTokens,
  getAccessToken,
  getRefreshToken,
  hasSessionMaterial,
  onSessionCleared,
  setAccessToken,
  setRefreshToken,
} from "@/lib/auth/session";

describe("session token storage", () => {
  afterEach(() => {
    clearSessionTokens();
  });

  it("keeps the access token in memory only", () => {
    setAccessToken("access");
    expect(getAccessToken()).toBe("access");
    expect(window.sessionStorage.getItem("doculens.refresh_token")).toBeNull();
  });

  it("persists the refresh token in sessionStorage", () => {
    setRefreshToken("refresh");
    expect(getRefreshToken()).toBe("refresh");
    expect(window.sessionStorage.getItem("doculens.refresh_token")).toBe("refresh");
    expect(hasSessionMaterial()).toBe(true);
  });

  it("clears both tokens and notifies listeners", () => {
    setAccessToken("access");
    setRefreshToken("refresh");
    const listener = vi.fn();
    const unsubscribe = onSessionCleared(listener);

    clearSessionTokens();

    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
    expect(hasSessionMaterial()).toBe(false);
    expect(listener).toHaveBeenCalledOnce();
    unsubscribe();
  });

  it("does not notify when clearing an empty session", () => {
    const listener = vi.fn();
    const unsubscribe = onSessionCleared(listener);
    clearSessionTokens();
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });
});
