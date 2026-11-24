"use client";

import { useState } from "react";

import type { SpanKind, SpanStatus, TraceSpan, TurnTrace } from "@/lib/protocol";
import { formatMs } from "@/lib/format";

const KIND_COLOR: Record<SpanKind, string> = {
  llm: "bg-violet",
  tool: "bg-accent",
  retrieval: "bg-teal",
  guardrail: "bg-success",
  voice: "bg-warning",
  system: "bg-subtle",
};

const STATUS_RING: Record<SpanStatus, string> = {
  ok: "",
  retry: "ring-2 ring-warning ring-offset-1 ring-offset-surface",
  error: "ring-2 ring-danger ring-offset-1 ring-offset-surface",
  blocked: "ring-2 ring-warning ring-offset-1 ring-offset-surface opacity-80",
};

export function SpanLegend() {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted">
      {(["llm", "tool", "retrieval", "guardrail"] as SpanKind[]).map((k) => (
        <span key={k} className="flex items-center gap-1">
          <span className={`inline-block size-2 rounded-sm ${KIND_COLOR[k]}`} /> {k}
        </span>
      ))}
      <span className="flex items-center gap-1">
        <span className="inline-block size-2 rounded-sm ring-2 ring-warning" /> retry/blocked
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block size-2 rounded-sm ring-2 ring-danger" /> error
      </span>
    </div>
  );
}

/** Gantt-style view of a turn's spans; click a span to inspect its attributes. */
export function SpanWaterfall({ trace }: { trace: TurnTrace }) {
  const [selected, setSelected] = useState<TraceSpan | null>(null);
  const total = Math.max(
    trace.latency.total_ms ?? 0,
    ...trace.spans.map((s) => s.start_ms + (s.duration_ms ?? 0)),
    1,
  );

  return (
    <div className="space-y-1">
      {trace.spans.map((s) => {
        const left = (s.start_ms / total) * 100;
        const width = Math.max(((s.duration_ms ?? 0) / total) * 100, 0.8);
        const isSel = selected?.id === s.id;
        return (
          <button
            key={s.id}
            type="button"
            onClick={() => setSelected(isSel ? null : s)}
            aria-pressed={isSel}
            className={`grid w-full grid-cols-[minmax(0,9.5rem)_1fr_3.5rem] items-center gap-2 rounded px-1 py-0.5 text-left text-[11px] hover:bg-surface-2 ${isSel ? "bg-surface-2" : ""}`}
          >
            <span className="truncate font-mono text-muted" title={s.name}>
              {s.name}
            </span>
            <span className="relative h-2.5 rounded-sm bg-surface-3/60">
              <span
                className={`absolute top-0 h-full rounded-sm ${KIND_COLOR[s.kind]} ${STATUS_RING[s.status]}`}
                style={{ left: `${Math.min(left, 99)}%`, width: `${Math.min(width, 100 - Math.min(left, 99))}%` }}
              />
            </span>
            <span className="text-right tabular-nums text-subtle">{formatMs(s.duration_ms)}</span>
          </button>
        );
      })}
      {selected && (
        <div className="mt-2 rounded-lg border border-border bg-surface-2 p-2">
          <div className="mb-1 flex items-center justify-between text-[11px]">
            <span className="font-mono font-medium">{selected.name}</span>
            <span className="text-subtle">
              {selected.kind} · {selected.status} · +{formatMs(selected.start_ms)}
            </span>
          </div>
          <pre className="scrollbar-thin max-h-72 overflow-auto font-mono text-[10.5px] leading-relaxed whitespace-pre-wrap break-words text-muted">
            {JSON.stringify(selected.attrs, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
