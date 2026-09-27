import type { ReactNode } from "react";

export interface AlertProps {
  tone?: "error" | "info";
  title?: string;
  id?: string;
  children: ReactNode;
}

const toneClass = {
  error: "border-red-200 bg-red-50 text-red-900",
  info: "border-slate-200 bg-slate-50 text-slate-800",
} as const;

export function Alert({ tone = "error", title, id, children }: AlertProps) {
  return (
    <div
      id={id}
      role={tone === "error" ? "alert" : "status"}
      className={`rounded-md border px-3 py-2 text-sm ${toneClass[tone]}`}
    >
      {title ? <p className="font-medium">{title}</p> : null}
      <div className={title ? "mt-1" : undefined}>{children}</div>
    </div>
  );
}
