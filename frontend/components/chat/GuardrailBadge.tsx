"use client";

import { ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { useId, useState } from "react";

import type { GuardrailResult } from "@/lib/protocol";
import { percent, titleCase } from "@/lib/format";

import { Badge, type Tone } from "../ui/Badge";

function tone(g: GuardrailResult): Tone {
  if (g.action === "retracted" || g.verdict === "ungrounded") return "danger";
  if (g.action === "flagged" || g.verdict === "error") return "warning";
  if (g.confidence >= 0.8) return "success";
  if (g.confidence >= 0.5) return "warning";
  return "danger";
}

/** Confidence badge with an expandable explanation of the two guardrail signals. */
export function GuardrailBadge({ guardrail }: { guardrail: GuardrailResult }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const t = tone(guardrail);
  const Icon = t === "success" ? ShieldCheck : t === "danger" ? ShieldAlert : ShieldQuestion;
  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={panelId}
        className="rounded-full focus-visible:outline-2 focus-visible:outline-accent"
      >
        <Badge tone={t}>
          <Icon className="size-3" />
          Confidence {percent(guardrail.confidence)}
          <span className="opacity-70">· {guardrail.verdict}</span>
        </Badge>
      </button>
      {open && (
        <div id={panelId} className="mt-1.5 rounded-lg border border-border bg-surface-2 p-2.5 text-xs text-muted">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt className="text-subtle">Retrieval</dt>
            <dd>
              {titleCase(guardrail.retrieval_band)}
              {guardrail.top_similarity !== null && ` (best cosine ${guardrail.top_similarity.toFixed(3)})`}
            </dd>
            <dt className="text-subtle">Groundedness</dt>
            <dd>
              {guardrail.verdict}
              {guardrail.groundedness !== null && ` · ${percent(guardrail.groundedness)} of claims supported`}
            </dd>
            <dt className="text-subtle">Action</dt>
            <dd>{guardrail.action}</dd>
          </dl>
          {guardrail.reasons.length > 0 && (
            <ul className="mt-2 list-disc space-y-0.5 pl-4">
              {guardrail.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
