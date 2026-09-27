/** Runtime configuration for the web app. Secrets must never use the NEXT_PUBLIC_ prefix. */

function requiredPublic(name: string, fallback: string): string {
  const value = process.env[name]?.trim();
  return value && value.length > 0 ? value : fallback;
}

export const appConfig = {
  apiBaseUrl: requiredPublic("NEXT_PUBLIC_API_BASE_URL", "http://localhost:8000").replace(
    /\/$/,
    "",
  ),
  requestIdHeader: "X-Request-ID",
} as const;
