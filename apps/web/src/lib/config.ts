/** Runtime configuration for the web app. Secrets must never use the NEXT_PUBLIC_ prefix. */

/**
 * Next.js only inlines `process.env.NEXT_PUBLIC_*` when the member is a static identifier.
 * Dynamic `process.env[name]` stays undefined in the browser bundle and silently uses fallbacks.
 */
function requiredPublic(value: string | undefined, fallback: string): string {
  const trimmed = value?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : fallback;
}

function publicInt(value: string | undefined, fallback: number): number {
  const raw = value?.trim();
  if (!raw) {
    return fallback;
  }
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export const appConfig = {
  apiBaseUrl: requiredPublic(process.env.NEXT_PUBLIC_API_BASE_URL, "http://localhost:8000").replace(
    /\/$/,
    "",
  ),
  requestIdHeader: "X-Request-ID",
  /** Client-side upload cap; must match or stay under API `MAX_FILE_SIZE_MB` (default 50). */
  maxFileSizeMb: publicInt(process.env.NEXT_PUBLIC_MAX_FILE_SIZE_MB, 50),
  /** Poll interval while a document is still processing (ms). */
  processingPollMs: publicInt(process.env.NEXT_PUBLIC_PROCESSING_POLL_MS, 2500),
} as const;

export const MEBIBYTE = 1024 * 1024;
