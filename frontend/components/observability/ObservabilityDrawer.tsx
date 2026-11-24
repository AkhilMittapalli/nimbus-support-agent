"use client";

import { Activity, X } from "lucide-react";

import type { ChaosConfig, TurnTrace } from "@/lib/protocol";
import { formatCost, formatMs, formatTokens } from "@/lib/format";

import { ChaosControls } from "./ChaosControls";
import { SpanLegend } from "./SpanWaterfall";
import { TraceCard } from "./TraceCard";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-surface-2 px-2.5 py-2">
      <div className="text-[10px] uppercase tracking-wide text-subtle">{label}</div>
      <div className="text-sm font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

export function ObservabilityDrawer({
  open,
  onClose,
  traces,
  selectedTurn,
  onSelectTurn,
  chaos,
  onChaosChange,
  onDemoReset,
}: {
  open: boolean;
  onClose: () => void;
  traces: TurnTrace[];
  selectedTurn: string | null;
  onSelectTurn: (turnId: string | null) => void;
  chaos: ChaosConfig;
  onChaosChange: (c: ChaosConfig) => void;
  onDemoReset: () => void;
}) {
  const totals = traces.reduce(
    (acc, t) => {
      acc.cost += t.totals.cost_usd;
      acc.tokens +=
        t.totals.input_tokens + t.totals.output_tokens + t.totals.audio_input_tokens + t.totals.audio_output_tokens;
      acc.calls += t.totals.calls;
      return acc;
    },
    { cost: 0, tokens: 0, calls: 0 },
  );
  const p50 = median(traces.map((t) => t.latency.total_ms).filter((v): v is number => v !== null));
  const ttft = median(
    traces.map((t) => t.latency.first_token_ms).filter((v): v is number => v !== null),
  );

  return (
    <>
      {/* Overlay backdrop on narrow screens */}
      <div
        className={`fixed inset-0 z-30 bg-black/30 transition-opacity lg:hidden ${open ? "opacity-100" : "pointer-events-none opacity-0"}`}
        onClick={onClose}
        aria-hidden
      />
      <aside
        aria-label="Observability"
        aria-hidden={!open}
        inert={!open}
        className={`fixed inset-y-0 right-0 z-40 flex w-[min(26rem,100vw)] flex-col border-l border-border bg-surface transition-transform duration-200 lg:static lg:z-auto lg:transition-[width,margin] ${
          open ? "translate-x-0 lg:w-[26rem]" : "translate-x-full lg:w-0 lg:translate-x-0 lg:overflow-hidden lg:border-l-0"
        }`}
      >
        <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-4">
          <Activity className="size-4 text-accent" />
          <h2 className="text-sm font-semibold">Observability</h2>
          <span className="text-xs text-subtle">{traces.length} turns</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close observability panel"
            className="ml-auto rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-text"
          >
            <X className="size-4" />
          </button>
        </header>
        <div className="scrollbar-thin flex-1 space-y-5 overflow-y-auto p-4">
          <section aria-label="Session totals" className="grid grid-cols-2 gap-2">
            <Stat label="Est. session cost" value={formatCost(totals.cost)} />
            <Stat label="Tokens" value={formatTokens(totals.tokens)} />
            <Stat label="Median turn latency" value={formatMs(p50)} />
            <Stat label="Median first token" value={formatMs(ttft)} />
          </section>

          <section className="space-y-2" aria-label="Turn traces">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Prompt traces</h3>
            </div>
            <SpanLegend />
            {traces.length === 0 ? (
              <p className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-subtle">
                Traces for each turn appear here: model calls, tool spans, retrieval scores, guardrail verdicts,
                latency, tokens and cost.
              </p>
            ) : (
              <div className="space-y-2">
                {traces
                  .map((t, i) => ({ t, i }))
                  .reverse()
                  .map(({ t, i }) => (
                    <TraceCard
                      key={t.turn_id}
                      trace={t}
                      index={i}
                      open={selectedTurn === t.turn_id}
                      onToggle={() => onSelectTurn(selectedTurn === t.turn_id ? null : t.turn_id)}
                    />
                  ))}
              </div>
            )}
          </section>

          <ChaosControls chaos={chaos} onChange={onChaosChange} onReset={onDemoReset} />
        </div>
      </aside>
    </>
  );
}
