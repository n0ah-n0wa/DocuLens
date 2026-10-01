import { beforeEach, describe, expect, it, vi } from "vitest";

describe("appConfig", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    Reflect.deleteProperty(process.env, "NEXT_PUBLIC_API_BASE_URL");
    vi.resetModules();
  });

  it("exposes a non-empty absolute API base URL without a trailing slash", async () => {
    const { appConfig } = await import("@/lib/config");
    expect(appConfig.apiBaseUrl).toMatch(/^https?:\/\//);
    expect(appConfig.apiBaseUrl.endsWith("/")).toBe(false);
  });

  it("defaults to the documented local API origin when env is unset", async () => {
    const { appConfig } = await import("@/lib/config");
    expect(appConfig.apiBaseUrl).toBe("http://localhost:8000");
  });

  it("honours NEXT_PUBLIC_API_BASE_URL when set", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test/");
    vi.resetModules();
    const { appConfig } = await import("@/lib/config");
    expect(appConfig.apiBaseUrl).toBe("https://api.example.test");
  });
});
