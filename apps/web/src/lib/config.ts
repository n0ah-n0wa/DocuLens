/** Runtime configuration for the web app. Secrets must never use the NEXT_PUBLIC_ prefix. */

function requiredPublic(name: string, fallback: string): string {
  const value = process.env[name]?.trim();
  return value && value.length > 0 ? value : fallback;
}

function publicInt(name: string, fallback: number): number {
  const raw = process.env[name]?.trim();
  if (!raw) {
    return fallback;
  }
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export const appConfig = {
  apiBaseUrl: requiredPublic("NEXT_PUBLIC_API_BASE_URL", "http://localhost:8000").replace(
    /\/$/,
    "",
  ),
  requestIdHeader: "X-Request-ID",
  /** Client-side upload cap; must match or stay under API `MAX_FILE_SIZE_MB` (default 50). */
  maxFileSizeMb: publicInt("NEXT_PUBLIC_MAX_FILE_SIZE_MB", 50),
  /** Poll interval while a document is still processing (ms). */
  processingPollMs: publicInt("NEXT_PUBLIC_PROCESSING_POLL_MS", 2500),
} as const;

export const MEBIBYTE = 1024 * 1024;
