import type { LabelHTMLAttributes, ReactNode } from "react";

export interface LabelProps extends LabelHTMLAttributes<HTMLLabelElement> {
  children: ReactNode;
}

export function Label({ children, className = "", ...rest }: LabelProps) {
  return (
    <label className={`block text-sm font-medium text-slate-700 ${className}`} {...rest}>
      {children}
    </label>
  );
}
