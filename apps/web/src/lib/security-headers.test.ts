import { describe, expect, it } from "vitest";

import nextConfig from "../../next.config";

describe("next.config security headers", () => {
  it("disables the powered-by header", () => {
    expect(nextConfig.poweredByHeader).toBe(false);
  });

  it("sets CSP, framing, and content-type protections for every path", async () => {
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
    expect(headers["Content-Security-Policy"]).toContain("http://localhost:8000");
  });
});
