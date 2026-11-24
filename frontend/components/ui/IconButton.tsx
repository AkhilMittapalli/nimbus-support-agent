import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "ghost" | "outline" | "primary" | "danger";

const VARIANTS: Record<Variant, string> = {
  ghost: "text-muted hover:text-text hover:bg-surface-2",
  outline: "border border-border bg-surface text-text hover:bg-surface-2",
  primary: "bg-accent text-accent-fg hover:opacity-90",
  danger: "bg-danger text-white hover:opacity-90",
};

export function Button({
  variant = "outline",
  size = "md",
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; children: ReactNode }) {
  const sizing = size === "sm" ? "h-8 px-2.5 text-xs gap-1.5" : "h-9 px-3 text-sm gap-2";
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center rounded-lg font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50 ${sizing} ${VARIANTS[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
