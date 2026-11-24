import { LifeBuoy } from "lucide-react";

import type { Ticket } from "@/lib/protocol";

import { Badge, type Tone } from "../ui/Badge";

const PRIORITY_TONE: Record<Ticket["priority"], Tone> = {
  low: "neutral",
  medium: "accent",
  high: "warning",
  urgent: "danger",
};

export function TicketCard({ ticket }: { ticket: Ticket }) {
  return (
    <div className="mt-2 rounded-lg border border-border bg-surface p-3 text-xs shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <LifeBuoy className="size-4 text-violet" aria-hidden />
        <span className="font-semibold text-text">Escalated to human support</span>
        <span className="font-mono text-muted">{ticket.id}</span>
        <Badge tone={PRIORITY_TONE[ticket.priority]}>{ticket.priority}</Badge>
        <Badge>{ticket.category}</Badge>
      </div>
      <p className="mt-1.5 text-muted">
        <span className="text-subtle">Reason:</span> {ticket.reason}
      </p>
    </div>
  );
}
