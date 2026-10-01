import type { NextConfig } from "next";

/**
 * Hosting and rendering mode (static export vs. server rendering) are undecided:
 * see docs/planning/open-questions.md, OQ-19.
 *
 * Security headers harden the browser surface regardless of hosting choice. CSP allows the
 * Next.js runtime (inline scripts/styles) and the configured API origin only.
 */
const configuredApi = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
const apiBaseUrl = (configuredApi && configuredApi.length > 0
  ? configuredApi
  : "http://localhost:8000"
).replace(/\/$/, "");

/** Localhost and 127.0.0.1 are distinct CSP origins; allow both when targeting loopback. */
function connectSrcOrigins(apiOrigin: string): string {
  const origins = new Set<string>([apiOrigin]);
  try {
    const url = new URL(apiOrigin);
    if (url.hostname === "localhost") {
      origins.add(`${url.protocol}//127.0.0.1${url.port ? `:${url.port}` : ""}`);
    } else if (url.hostname === "127.0.0.1") {
      origins.add(`${url.protocol}//localhost${url.port ? `:${url.port}` : ""}`);
    }
  } catch {
    // Keep the configured origin only when it is not a parseable absolute URL.
  }
  return [...origins].join(" ");
}

const contentSecurityPolicy = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  `connect-src 'self' ${connectSrcOrigins(apiBaseUrl)}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=()",
  },
  { key: "X-Permitted-Cross-Domain-Policies", value: "none" },
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  ...(process.env.NODE_ENV === "production"
    ? [
        {
          key: "Strict-Transport-Security",
          value: "max-age=31536000; includeSubDomains",
        },
      ]
    : []),
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // CD sets DOCULENS_STATIC_EXPORT=1 for S3+CloudFront (OQ-19 provisional).
  // Static export cannot use next.config headers(); CloudFront/S3 can add them later.
  ...(process.env.DOCULENS_STATIC_EXPORT === "1"
    ? {
        output: "export" as const,
        images: { unoptimized: true },
        trailingSlash: true,
      }
    : {
        async headers() {
          return [
            {
              source: "/:path*",
              headers: securityHeaders,
            },
          ];
        },
      }),
};

export default nextConfig;
