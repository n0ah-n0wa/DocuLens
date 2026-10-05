"use client";

import Link from "next/link";

import { Spinner } from "@/components/ui/spinner";
import { useAuth } from "@/lib/auth/auth-context";

export function HomeLanding() {
  const { status } = useAuth();

  if (status === "booting") {
    return (
      <main
        className="flex min-h-screen items-center justify-center"
        role="status"
        aria-live="polite"
      >
        <Spinner label="Loading DocuLens" />
      </main>
    );
  }

  if (status === "authenticated") {
    return (
      <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-4 px-4 py-16">
        <h1 className="text-3xl font-semibold tracking-tight">DocuLens</h1>
        <p className="text-lg text-slate-600">You are signed in. Continue to your dashboard.</p>
        <div>
          <Link
            href="/dashboard"
            className="inline-flex items-center justify-center rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
          >
            Open dashboard
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-6 px-4 py-16">
      <div className="space-y-3">
        <h1 className="text-3xl font-semibold tracking-tight">DocuLens</h1>
        <p className="text-lg text-slate-600">
          Grounded document Q&amp;A: upload PDFs, organise collections, and get answers with
          inspectable source citations — not free-form invention.
        </p>
      </div>
      <div className="flex flex-wrap gap-3">
        <Link
          href="/login"
          className="inline-flex items-center justify-center rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
        >
          Sign in
        </Link>
        <Link
          href="/register"
          className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-900 hover:bg-slate-50"
        >
          Create account
        </Link>
      </div>
    </main>
  );
}
