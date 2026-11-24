"use client";

import { ChevronDown, Keyboard, Mic } from "lucide-react";
import { useEffect, useRef } from "react";

import type { TurnTrace } from "@/lib/protocol";
import { formatCost, formatMs, formatTokens } from "@/lib/format";

import { Badge } from "../ui/Badge";
import { SpanWaterfall } from "./SpanWaterfall";

function outcomeNumber(trace: TurnTrace, key: string): number | null {
  const v = trace.outcome[key];
  return typeof v === "number" ? v : null;
}

export function TraceCard({
  trace,
  index,
  open,
  onToggle,
}: {
  trace: TurnTrace;
  index: number;
  open: boolean;
  onToggle: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [open]);

  const t = trace.totals;
  const voiceLatency = outcomeNumber(trace, "voice_response_latency_ms");
  const status = typeof trace.outcome.status === "string" ? trace.outcome.status : "ok";
  const errorCode = typeof trace.outcome.error_code === "string" ? trace.outcome.error_code : null;
  const audioTokens = t.audio_input_tokens + t.audio_output_tokens;

  return (
    <div ref={ref} className={`rounded-xl border bg-surface ${open ? "border-accent/50" : "border-border"}`}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span className="text-[11px] text-subtle tabular-nums">#{index + 1}</span>
        {trace.mode === "voice" ? (
          <Mic className="size-3.5 text-violet" aria-label="voice turn" />
        ) : (
          <Keyboard className="size-3.5 text-accent" aria-label="text turn" />
        )}
        <span className="flex-1 truncate text-xs font-medium tabular-nums">
          {formatMs(trace.latency.total_ms)}
          <span className="font-normal text-subtle">
            {" "}
            · {formatTokens(t.input_tokens + t.output_tokens + audioTokens)} tok · {formatCost(t.cost_usd)}
          </span>
        </span>
        {status !== "ok" && <Badge tone="danger">{errorCode ?? status}</Badge>}
        <ChevronDown className={`size-4 text-subtle transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="space-y-3 border-t border-border px-3 py-3">
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
            <dt className="text-subtle">{trace.mode === "voice" ? "Voice response latency" : "First token"}</dt>
            <dd className="text-right tabular-nums">
              {formatMs(trace.mode === "voice" ? (voiceLatency ?? trace.latency.first_token_ms) : trace.latency.first_token_ms)}
            </dd>
            <dt className="text-subtle">End-to-end</dt>
            <dd className="text-right tabular-nums">{formatMs(trace.latency.total_ms)}</dd>
            <dt className="text-subtle">Model calls</dt>
            <dd className="text-right tabular-nums">{t.calls}</dd>
            <dt className="text-subtle">Tokens in / out</dt>
            <dd className="text-right tabular-nums">
              {t.input_tokens.toLocaleString()} / {t.output_tokens.toLocaleString()}
              {t.thinking_tokens > 0 && <span className="text-subtle"> (+{t.thinking_tokens} thinking)</span>}
            </dd>
            {audioTokens > 0 && (
              <>
                <dt className="text-subtle">Audio tokens in / out</dt>
                <dd className="text-right tabular-nums">
                  {t.audio_input_tokens.toLocaleString()} / {t.audio_output_tokens.toLocaleString()}
                </dd>
              </>
            )}
            <dt className="text-subtle">Est. cost</dt>
            <dd className="text-right tabular-nums">{formatCost(t.cost_usd)}</dd>
          </dl>
          {Object.keys(trace.usage).length > 0 && (
            <div className="space-y-0.5 text-[10.5px] text-muted">
              {Object.entries(trace.usage).map(([model, u]) => (
                <div key={model} className="flex justify-between gap-2 font-mono">
                  <span className="truncate">{model}</span>
                  <span className="shrink-0 tabular-nums">
                    {u.calls}× · {formatTokens(u.input_tokens + u.audio_input_tokens)}→
                    {formatTokens(u.output_tokens + u.thinking_tokens + u.audio_output_tokens)} · {formatCost(u.cost_usd)}
                  </span>
                </div>
              ))}
            </div>
          )}
          <SpanWaterfall trace={trace} />
        </div>
      )}
    </div>
  );
}
