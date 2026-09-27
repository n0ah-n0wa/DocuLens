import type { ProcessingStatus } from "@doculens/shared-types";

import {
  processingStatusLabel,
  processingStatusTone,
  shouldPollProcessingStatus,
} from "@/lib/documents/status";

const toneClass = {
  neutral: "bg-slate-100 text-slate-700",
  progress: "bg-amber-50 text-amber-900",
  success: "bg-emerald-50 text-emerald-900",
  danger: "bg-red-50 text-red-900",
} as const;

export function ProcessingStatusBadge({ status }: { status: ProcessingStatus }) {
  const tone = processingStatusTone(status);
  const label = processingStatusLabel(status);
  const live = shouldPollProcessingStatus(status);

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium ${toneClass[tone]}`}
      aria-label={`Processing status: ${label}`}
      // Announce transitions while the pipeline is still running.
      aria-live={live ? "polite" : undefined}
    >
      {live ? (
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" aria-hidden="true" />
      ) : null}
      <span>{label}</span>
    </span>
  );
}
