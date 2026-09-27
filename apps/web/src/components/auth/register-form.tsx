"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";

import { Alert } from "@/components/ui/alert";
import { AuthShell } from "@/components/ui/auth-shell";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { messageForApiError } from "@/lib/api/errors";
import { useAuth } from "@/lib/auth/auth-context";
import { registerCredentialsSchema } from "@/lib/validation/credentials";
import {
  fieldErrorsFromApiError,
  fieldErrorsFromZod,
  type CredentialFieldErrors,
} from "@/lib/validation/form-errors";

export function RegisterForm() {
  const { register } = useAuth();
  const router = useRouter();
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

    const parsed = registerCredentialsSchema.safeParse({ email, password });
    if (!parsed.success) {
      setFieldErrors(fieldErrorsFromZod(parsed.error));
      return;
    }

    setSubmitting(true);
    try {
      await register(parsed.data);
      router.replace("/dashboard");
    } catch (error) {
      const apiFields = fieldErrorsFromApiError(error);
      if (Object.keys(apiFields).length > 0) {
        setFieldErrors(apiFields);
      }
      setFormError(messageForApiError(error, "Unable to create your account."));
      queueMicrotask(() => {
        alertRef.current?.focus();
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell
      title="Create an account"
      subtitle="Register with your email to start uploading documents."
      footer={
        <>
          Already registered?{" "}
          <Link
            className="font-medium text-slate-900 underline-offset-2 hover:underline"
            href="/login"
          >
            Sign in
          </Link>
        </>
      }
    >
      <form className="space-y-4" onSubmit={(event) => void onSubmit(event)} noValidate>
        {formError ? (
          <div ref={alertRef} tabIndex={-1} className="outline-none">
            <Alert title="Registration failed">{formError}</Alert>
          </div>
        ) : null}
        <FormField
          id="register-email"
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
          id="register-password"
          label="Password"
          name="password"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={fieldErrors.password}
          hint="At least 12 characters. Do not reuse your email."
          required
        />
        <Button type="submit" className="w-full" loading={submitting}>
          {submitting ? "Creating account…" : "Create account"}
        </Button>
      </form>
    </AuthShell>
  );
}
