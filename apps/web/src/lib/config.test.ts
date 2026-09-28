import { describe, expect, it } from "vitest";

import { appConfig } from "@/lib/config";

describe("appConfig", () => {
  it("exposes a non-empty absolute API base URL without a trailing slash", () => {
    expect(appConfig.apiBaseUrl).toMatch(/^https?:\/\//);
    expect(appConfig.apiBaseUrl.endsWith("/")).toBe(false);
  });

  it("defaults to the documented local API origin when env is unset", () => {
    // Vitest loads this module without NEXT_PUBLIC_API_BASE_URL; the static
    // process.env.NEXT_PUBLIC_API_BASE_URL access must still resolve to the fallback.
    expect(appConfig.apiBaseUrl).toBe("http://localhost:8000");
  });
});
