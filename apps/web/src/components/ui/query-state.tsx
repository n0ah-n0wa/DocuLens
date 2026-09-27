import type { ReactNode } from "react";

import { Alert } from "@/components/ui/alert";
import { Spinner } from "@/components/ui/spinner";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {description ? <p className="max-w-2xl text-sm text-slate-600">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return (
    <div
      className="flex min-h-48 items-center justify-center rounded-md border border-dashed border-slate-200 bg-white"
      role="status"
      aria-live="polite"
    >
      <Spinner label={label} />
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  message,
  action,
}: {
  title?: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <div className="space-y-3">
      <Alert title={title}>{message}</Alert>
      {action}
    </div>
  );
}

export function EmptyState({
  title,
  message,
  action,
}: {
  title: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <div
      className="flex min-h-48 flex-col items-start justify-center gap-3 rounded-md border border-dashed border-slate-200 bg-white px-6 py-8"
      role="status"
    >
      <div className="space-y-1">
        <p className="text-sm font-medium text-slate-900">{title}</p>
        <p className="max-w-lg text-sm text-slate-600">{message}</p>
      </div>
      {action}
    </div>
  );
}
