"use client";

import { Activity, AlertTriangle, Bot, Loader2, Mic, RotateCw, ShieldOff, Square } from "lucide-react";

import type { AssistantItem, UserItem } from "@/lib/conversation";

import { Badge } from "../ui/Badge";
import { Button } from "../ui/IconButton";
import { GuardrailBadge } from "./GuardrailBadge";
import { Markdown } from "./Markdown";
import { StepTimeline } from "./StepTimeline";
import { TicketCard } from "./TicketCard";

export function UserBubble({ item }: { item: UserItem }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] sm:max-w-[75%]">
        <div
          className={`rounded-2xl rounded-br-md px-3.5 py-2 text-sm whitespace-pre-wrap break-words ${
            item.live ? "bg-accent/70 text-accent-fg italic" : "bg-accent text-accent-fg"
          }`}
        >
          {item.text}
        </div>
        {item.mode === "voice" && (
          <div className="mt-1 flex justify-end">
            <Badge tone="accent" title="Spoken via voice mode">
              <Mic className="size-3" /> {item.live ? "listening…" : "voice"}
            </Badge>
          </div>
        )}
      </div>
    </div>
  );
}

const REPLACE_REASONS: Record<string, string> = {
  ungrounded: "The guardrail retracted the draft answer because it couldn't be verified against policy.",
  step_limit: "The agent hit its step limit, so the case was handed to a human.",
  empty_answer: "The model returned no answer, so the case was handed to a human.",
  off_topic: "The guardrail withdrew a reply that went outside Nimbus Cloud support.",
};

export function AssistantBubble({
  item,
  onRetry,
  onShowTrace,
}: {
  item: AssistantItem;
  onRetry: (id: string) => void;
  onShowTrace: (turnId: string) => void;
}) {
  const waiting = item.streaming && !item.text;
  return (
    <div className="flex gap-2.5">
      <div
        className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-violet-soft text-violet"
        aria-hidden
      >
        <Bot className="size-4" />
      </div>
      <div className="min-w-0 max-w-[92%] flex-1 sm:max-w-[80%]">
        <StepTimeline steps={item.steps} />

        {item.notices.map((n, i) => (
          <div
            key={`${n}-${i}`}
            role="status"
            className="mb-2 flex items-center gap-2 rounded-lg bg-warning-soft px-2.5 py-1.5 text-xs text-warning"
          >
            <AlertTriangle className="size-3.5 shrink-0" /> {n}
          </div>
        ))}

        {item.replacedReason && (
          <div className="mb-2 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
            <ShieldOff className="mt-0.5 size-3.5 shrink-0" />
            <span>
              <strong>Guardrail retracted the draft.</strong>{" "}
              {REPLACE_REASONS[item.replacedReason] ?? item.replacedReason}
            </span>
          </div>
        )}

        {(item.text || waiting) && (
          <div className="rounded-2xl rounded-tl-md border border-border bg-surface px-3.5 py-2.5 text-sm shadow-card">
            {waiting ? (
              <span className="flex items-center gap-2 text-muted" role="status">
                <Loader2 className="size-3.5 animate-spin" />
                {item.status ?? "Thinking…"}
              </span>
            ) : (
              <div className={`prose-chat break-words ${item.streaming && item.mode === "text" ? "cursor-blink" : ""}`}>
                <Markdown text={item.text} />
              </div>
            )}
          </div>
        )}

        {item.streaming && item.text && item.status && (
          <p className="mt-1 flex items-center gap-1.5 text-xs text-muted" role="status">
            <Loader2 className="size-3 animate-spin" /> {item.status}
          </p>
        )}

        {item.tickets.map((t) => (
          <TicketCard key={t.id} ticket={t} />
        ))}

        {item.error && (
          <div
            role="alert"
            className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger"
          >
            <AlertTriangle className="size-4 shrink-0" />
            <span className="flex-1">{item.error.message}</span>
            <code className="text-[10px] opacity-70">{item.error.code}</code>
            {item.error.retryable && item.userId && item.mode === "text" && (
              <Button size="sm" variant="outline" onClick={() => onRetry(item.id)}>
                <RotateCw className="size-3.5" /> Retry
              </Button>
            )}
          </div>
        )}

        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          {item.mode === "voice" && (
            <Badge tone="violet">
              <Mic className="size-3" /> voice
            </Badge>
          )}
          {item.interrupted && <Badge tone="warning">interrupted</Badge>}
          {item.stopped && (
            <Badge>
              <Square className="size-2.5" /> stopped
            </Badge>
          )}
          {item.turnId && !item.streaming && (
            <button
              type="button"
              onClick={() => onShowTrace(item.turnId as string)}
              className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] text-subtle hover:bg-surface-2 hover:text-text"
              aria-label="Show trace for this turn"
            >
              <Activity className="size-3" /> trace
            </button>
          )}
        </div>
        {item.guardrail && <GuardrailBadge guardrail={item.guardrail} />}
      </div>
    </div>
  );
}
