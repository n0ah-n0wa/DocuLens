"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useRef, useState, type FormEvent } from "react";

import { Alert } from "@/components/ui/alert";
import { AuthShell } from "@/components/ui/auth-shell";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { messageForApiError } from "@/lib/api/errors";
import { useAuth } from "@/lib/auth/auth-context";
import { credentialsSchema } from "@/lib/validation/credentials";
import {
  fieldErrorsFromApiError,
  fieldErrorsFromZod,
  type CredentialFieldErrors,
} from "@/lib/validation/form-errors";

export function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const formErrorId = useId();
  const alertRef = useRef<HTMLDivElement>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<CredentialFieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    setFieldErrors({});

    const parsed = credentialsSchema.safeParse({ email, password });
    if (!parsed.success) {
      setFieldErrors(fieldErrorsFromZod(parsed.error));
      return;
    }

    setSubmitting(true);
    try {
      await login(parsed.data);
      router.replace("/dashboard");
    } catch (error) {
      const apiFields = fieldErrorsFromApiError(error);
      if (Object.keys(apiFields).length > 0) {
        setFieldErrors(apiFields);
      }
      setFormError(messageForApiError(error, "Unable to sign in."));
      queueMicrotask(() => {
        alertRef.current?.focus();
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell
      title="Sign in"
      subtitle="Access your document collections and grounded answers."
      footer={
        <>
          No account yet?{" "}
          <Link
            className="font-medium text-slate-900 underline-offset-2 hover:underline"
            href="/register"
          >
            Create one
          </Link>
        </>
      }
    >
      <form className="space-y-4" onSubmit={(event) => void onSubmit(event)} noValidate>
        {formError ? (
          <div ref={alertRef} tabIndex={-1} className="outline-none">
            <Alert title="Sign-in failed" id={formErrorId}>
              {formError}
            </Alert>
          </div>
        ) : null}
        <FormField
          id="login-email"
          label="Email"
          name="email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          error={fieldErrors.email}
          required
        />
        <FormField
          id="login-password"
          label="Password"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={fieldErrors.password}
          required
        />
        <Button type="submit" className="w-full" loading={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </AuthShell>
  );
}
