import type { ReactNode } from "react";

export type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "violet" | "teal";

const TONES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted border-border",
  accent: "bg-accent-soft text-accent border-transparent",
  success: "bg-success-soft text-success border-transparent",
  warning: "bg-warning-soft text-warning border-transparent",
  danger: "bg-danger-soft text-danger border-transparent",
  violet: "bg-violet-soft text-violet border-transparent",
  teal: "bg-teal-soft text-teal border-transparent",
};

export function Badge({
  tone = "neutral",
  children,
  className = "",
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 whitespace-nowrap ${TONES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

export const TIER_TONE: Record<string, Tone> = {
  free: "neutral",
  starter: "teal",
  pro: "accent",
  enterprise: "violet",
};
