import { ZodError } from "zod";

import { ApiError } from "@/lib/api/errors";

export type CredentialField = "email" | "password";

export type CredentialFieldErrors = Partial<Record<CredentialField, string>>;

export function fieldErrorsFromZod(error: ZodError): CredentialFieldErrors {
  const next: CredentialFieldErrors = {};
  for (const issue of error.issues) {
    const key = issue.path[0];
    if ((key === "email" || key === "password") && next[key] === undefined) {
      next[key] = issue.message;
    }
  }
  return next;
}

function credentialFieldFromLocation(location: string): CredentialField | null {
  const normalized = location.toLowerCase();
  if (
    normalized === "email" ||
    normalized.endsWith(".email") ||
    normalized.includes("body.email")
  ) {
    return "email";
  }
  if (
    normalized === "password" ||
    normalized.endsWith(".password") ||
    normalized.includes("body.password")
  ) {
    return "password";
  }
  return null;
}

/** Map §35 `details[].location` values that point at credential fields onto form fields. */
export function fieldErrorsFromApiError(error: unknown): CredentialFieldErrors {
  if (!(error instanceof ApiError) || error.details === undefined) {
    return {};
  }
  const next: CredentialFieldErrors = {};
  for (const detail of error.details) {
    const field = credentialFieldFromLocation(detail.location);
    if (field !== null && next[field] === undefined) {
      next[field] = detail.message;
    }
  }
  return next;
}
