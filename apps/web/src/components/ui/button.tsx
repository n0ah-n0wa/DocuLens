import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const variantClass: Record<ButtonVariant, string> = {
  primary:
    "bg-slate-900 text-white hover:bg-slate-800 disabled:bg-slate-400 focus-visible:outline-slate-900",
  secondary:
    "border border-slate-300 bg-white text-slate-900 hover:bg-slate-50 disabled:text-slate-400 focus-visible:outline-slate-400",
  ghost:
    "text-slate-700 hover:bg-slate-100 disabled:text-slate-400 focus-visible:outline-slate-400",
  danger:
    "bg-red-700 text-white hover:bg-red-800 disabled:bg-red-300 focus-visible:outline-red-700",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  loading?: boolean;
  children: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "primary",
    loading = false,
    disabled,
    children,
    className = "",
    type = "button",
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled === true || loading}
      aria-busy={loading || undefined}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed ${variantClass[variant]} ${className}`}
      {...rest}
    >
      {loading ? <span className="sr-only">Working</span> : null}
      {children}
    </button>
  );
});
