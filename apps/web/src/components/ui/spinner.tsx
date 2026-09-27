export interface SpinnerProps {
  label?: string;
  className?: string;
}

export function Spinner({ label = "Loading", className = "" }: SpinnerProps) {
  return (
    <div className={`flex flex-col items-center gap-3 text-slate-600 ${className}`}>
      <span
        className="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-slate-900"
        aria-hidden="true"
      />
      <span className="text-sm">{label}</span>
    </div>
  );
}
