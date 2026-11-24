import { LifeBuoy, ShieldCheck, ShieldQuestion } from "lucide-react";

import type { CustomerView, Ticket } from "@/lib/protocol";

import { Badge, TIER_TONE } from "../ui/Badge";

/** Session context strip: who the agent is talking to and any open escalations. */
export function CustomerCard({ customer, tickets }: { customer: CustomerView | null; tickets: Ticket[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border bg-surface/70 px-4 py-2 text-xs backdrop-blur">
      {customer ? (
        <>
          <span className="flex items-center gap-1.5 font-medium">
            <ShieldCheck className="size-3.5 text-success" aria-hidden /> {customer.name}
          </span>
          <span className="hidden text-muted sm:inline">{customer.email}</span>
          <Badge tone={TIER_TONE[customer.tier] ?? "neutral"}>{customer.tier}</Badge>
          <Badge tone={customer.contract_status === "active" ? "success" : "warning"}>
            contract {customer.contract_status.replace("_", " ")}
          </Badge>
          {customer.contract_end && (
            <span className="hidden text-subtle md:inline">renews {customer.contract_end}</span>
          )}
        </>
      ) : (
        <span className="flex items-center gap-1.5 text-muted">
          <ShieldQuestion className="size-3.5" aria-hidden /> Customer not verified
        </span>
      )}
      {tickets.length > 0 && (
        <span className="ml-auto flex items-center gap-1.5 text-violet">
          <LifeBuoy className="size-3.5" aria-hidden />
          {tickets.length === 1 ? `Escalated · ${tickets[0].id}` : `${tickets.length} open tickets`}
        </span>
      )}
    </div>
  );
}
