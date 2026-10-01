import { beforeEach, describe, expect, it, vi } from "vitest";

describe("next.config security headers", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    Reflect.deleteProperty(process.env, "DOCULENS_STATIC_EXPORT");
    Reflect.deleteProperty(process.env, "NEXT_PUBLIC_API_BASE_URL");
    vi.resetModules();
  });

  it("disables the powered-by header", async () => {
    const { default: nextConfig } = await import("../../next.config");
    expect(nextConfig.poweredByHeader).toBe(false);
  });

  it("sets CSP, framing, and content-type protections for every path", async () => {
    const { default: nextConfig } = await import("../../next.config");
    const headersFn = nextConfig.headers;
    expect(headersFn).toBeTypeOf("function");
    const entries = await headersFn!();
    expect(entries).toHaveLength(1);
    expect(entries[0]?.source).toBe("/:path*");

    const headers = Object.fromEntries(
      (entries[0]?.headers ?? []).map((header) => [header.key, header.value]),
    );

    expect(headers["X-Content-Type-Options"]).toBe("nosniff");
    expect(headers["X-Frame-Options"]).toBe("DENY");
    expect(headers["Referrer-Policy"]).toBe("no-referrer");
    expect(headers["Permissions-Policy"]).toContain("camera=()");
    expect(headers["Content-Security-Policy"]).toContain("frame-ancestors 'none'");
    expect(headers["Content-Security-Policy"]).toContain("connect-src 'self'");
    // Loopback aliases are both allowed so CI (127.0.0.1) and local (localhost) stay aligned.
    expect(headers["Content-Security-Policy"]).toContain("http://localhost:8000");
    expect(headers["Content-Security-Policy"]).toContain("http://127.0.0.1:8000");
  });

  it("omits next.config headers under static export (CloudFront supplies them)", async () => {
    vi.stubEnv("DOCULENS_STATIC_EXPORT", "1");
    vi.resetModules();
    const { default: nextConfig } = await import("../../next.config");
    expect(nextConfig.output).toBe("export");
    expect(nextConfig.headers).toBeUndefined();
  });
});
