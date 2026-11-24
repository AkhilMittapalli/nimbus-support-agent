"use client";

import { Ban, Check, Loader2, RotateCcw, X } from "lucide-react";

import type { Step } from "@/lib/conversation";
import { formatMs } from "@/lib/format";

import { toolMeta } from "./toolMeta";

function StatusIcon({ status }: { status: Step["status"] }) {
  switch (status) {
    case "running":
      return <Loader2 className="size-3.5 animate-spin text-accent" aria-label="running" />;
    case "ok":
      return <Check className="size-3.5 text-success" aria-label="completed" />;
    case "blocked":
      return <Ban className="size-3.5 text-warning" aria-label="blocked by policy" />;
    case "error":
      return <X className="size-3.5 text-danger" aria-label="failed" />;
  }
}

function formatArgs(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (entries.length === 0) return "";
  return entries
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ")
    .slice(0, 140);
}

/** Live tool-call chain for one assistant turn. */
export function StepTimeline({ steps }: { steps: Step[] }) {
  if (steps.length === 0) return null;
  return (
    <ol className="mb-2 space-y-1" aria-label="Agent steps">
      {steps.map((step, i) => {
        const { icon: Icon, name } = toolMeta(step.tool);
        const args = formatArgs(step.args);
        return (
          <li
            key={step.id}
            className="group relative flex items-start gap-2.5 rounded-lg border border-border bg-surface-2/60 px-2.5 py-1.5 text-xs"
          >
            <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-md bg-surface text-muted">
              <Icon className="size-3.5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="text-subtle tabular-nums">{i + 1}.</span>
                <span className="font-medium text-text">{step.status === "running" ? step.label : name}</span>
                {step.retries.length > 0 && (
                  <span
                    className="inline-flex items-center gap-1 rounded-full bg-warning-soft px-1.5 text-[10px] font-medium text-warning"
                    title={step.retries.join("\n")}
                  >
                    <RotateCcw className="size-2.5" /> retried ×{step.retries.length}
                  </span>
                )}
              </div>
              {step.summary && (
                <p
                  className={
                    step.status === "error" ? "text-danger" : step.status === "blocked" ? "text-warning" : "text-muted"
                  }
                >
                  {step.summary}
                </p>
              )}
              {args && <p className="truncate font-mono text-[10.5px] text-subtle" title={args}>{args}</p>}
            </div>
            <span className="flex shrink-0 items-center gap-1.5 text-subtle tabular-nums">
              {step.durationMs !== null && formatMs(step.durationMs)}
              <StatusIcon status={step.status} />
            </span>
          </li>
        );
      })}
    </ol>
  );
}
