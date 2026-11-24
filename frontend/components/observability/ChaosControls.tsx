"use client";

import { FlaskConical, RotateCcw } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";
import { TOOL_NAMES, type ChaosConfig, type LLMFault } from "@/lib/protocol";

import { Button } from "../ui/IconButton";

const LLM_FAULTS: { value: LLMFault; label: string }[] = [
  { value: "none", label: "None" },
  { value: "rate_limit_once", label: "429 once → retry" },
  { value: "timeout_once", label: "Timeout once → retry" },
  { value: "outage", label: "Full outage → fallback UI" },
];

const selectCls =
  "h-8 w-full rounded-lg border border-border bg-surface px-2 text-xs text-text focus-visible:outline-2 focus-visible:outline-accent";

/** Fault injection to demonstrate graceful degradation (backend honours it only with ENABLE_CHAOS). */
export function ChaosControls({
  chaos,
  onChange,
  onReset,
}: {
  chaos: ChaosConfig;
  onChange: (c: ChaosConfig) => void;
  onReset: () => void;
}) {
  const [resetState, setResetState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const active = chaos.tool_failure !== null || chaos.llm_fault !== "none";

  return (
    <section aria-labelledby="chaos-heading" className="space-y-2.5">
      <h3 id="chaos-heading" className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
        <FlaskConical className="size-3.5" /> Resilience testing
        {active && <span className="ml-auto rounded-full bg-warning-soft px-1.5 text-[10px] text-warning normal-case">active</span>}
      </h3>
      <div className="grid grid-cols-2 gap-2">
        <label className="col-span-2 space-y-1 text-[11px] text-muted">
          <span>Tool failure</span>
          <select
            className={selectCls}
            value={chaos.tool_failure ?? ""}
            onChange={(e) => onChange({ ...chaos, tool_failure: e.target.value || null })}
          >
            <option value="">None</option>
            <option value="any">Any tool</option>
            {TOOL_NAMES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-[11px] text-muted">
          <span>Failure mode</span>
          <select
            className={selectCls}
            value={chaos.tool_failure_mode}
            disabled={!chaos.tool_failure}
            onChange={(e) => onChange({ ...chaos, tool_failure_mode: e.target.value === "always" ? "always" : "once" })}
          >
            <option value="once">Once (retry recovers)</option>
            <option value="always">Always (degrade)</option>
          </select>
        </label>
        <label className="space-y-1 text-[11px] text-muted">
          <span>LLM fault (chat)</span>
          <select
            className={selectCls}
            value={chaos.llm_fault}
            onChange={(e) => onChange({ ...chaos, llm_fault: e.target.value as LLMFault })}
          >
            {LLM_FAULTS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="text-[10.5px] text-subtle">
        Applies to the next turn. Voice honours tool failures only (tool name sent on connect, failure mode always
        &quot;once&quot;).
      </p>
      <Button
        size="sm"
        className="w-full"
        disabled={resetState === "busy"}
        onClick={async () => {
          setResetState("busy");
          try {
            await api.resetDemo();
            setResetState("done");
            onReset();
          } catch {
            setResetState("error");
          }
        }}
      >
        <RotateCcw className="size-3.5" />
        {resetState === "busy"
          ? "Resetting…"
          : resetState === "done"
            ? "Demo data reset ✓"
            : resetState === "error"
              ? "Reset failed - retry"
              : "Reset demo data (refunds, tickets)"}
      </Button>
    </section>
  );
}
