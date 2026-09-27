import { forwardRef, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & {
  invalid?: boolean;
};

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className = "", invalid = false, id, children, ...rest },
  ref,
) {
  return (
    <select
      ref={ref}
      id={id}
      aria-invalid={invalid || undefined}
      className={`w-full rounded-md border bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition focus:ring-2 focus:ring-slate-900/20 ${
        invalid ? "border-red-500" : "border-slate-300 focus:border-slate-500"
      } ${className}`}
      {...rest}
    >
      {children}
    </select>
  );
});

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  invalid?: boolean;
};

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className = "", invalid = false, id, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      id={id}
      aria-invalid={invalid || undefined}
      className={`w-full rounded-md border bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition placeholder:text-slate-400 focus:ring-2 focus:ring-slate-900/20 ${
        invalid ? "border-red-500" : "border-slate-300 focus:border-slate-500"
      } ${className}`}
      {...rest}
    />
  );
});
