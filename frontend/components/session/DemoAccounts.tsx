"use client";

import { KeyRound, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, type DemoCustomer } from "@/lib/api";

import { Badge, TIER_TONE } from "../ui/Badge";
import { Button } from "../ui/IconButton";

/** Popover listing seeded mock customers; clicking one inserts its credentials into the composer. */
export function DemoAccounts({ onInsert }: { onInsert: (text: string) => void }) {
  const [open, setOpen] = useState(false);
  const [customers, setCustomers] = useState<DemoCustomer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || customers) return;
    let cancelled = false;
    api
      .demoCustomers()
      .then((r) => !cancelled && setCustomers(r.customers))
      .catch(() => !cancelled && setError("Demo accounts unavailable."));
    return () => {
      cancelled = true;
    };
  }, [open, customers]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <Button size="sm" variant="ghost" onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label="Demo accounts"
        title="Demo accounts"
      >
        <KeyRound className="size-4" /> <span className="hidden xl:inline">Demo accounts</span>
      </Button>
      {open && (
        <div
          role="dialog"
          aria-label="Demo accounts"
          className="absolute right-0 z-50 mt-2 w-[min(24rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-2 shadow-xl"
        >
          <p className="px-2 pb-2 pt-1 text-[11px] text-muted">
            Mock customers for the demo. Click one to insert its email and PIN into the message box.
          </p>
          {!customers && !error && (
            <div className="flex justify-center p-4">
              <Loader2 className="size-4 animate-spin text-muted" />
            </div>
          )}
          {error && <p className="p-3 text-xs text-danger">{error}</p>}
          <ul className="space-y-1">
            {customers?.map((c) => (
              <li key={c.email}>
                <button
                  type="button"
                  onClick={() => {
                    onInsert(`Hi, I'm ${c.email}, PIN ${c.pin}. `);
                    setOpen(false);
                  }}
                  className="w-full rounded-lg px-2 py-2 text-left hover:bg-surface-2"
                >
                  <span className="flex flex-wrap items-center gap-1.5 text-xs">
                    <span className="font-medium">{c.name}</span>
                    <Badge tone={TIER_TONE[c.tier] ?? "neutral"}>{c.tier}</Badge>
                    {c.contract_status !== "active" && <Badge tone="warning">{c.contract_status}</Badge>}
                    <span className="ml-auto font-mono text-[11px] text-muted">PIN {c.pin}</span>
                  </span>
                  <span className="mt-0.5 block font-mono text-[11px] text-muted">{c.email}</span>
                  <span className="mt-0.5 block text-[11px] text-subtle">{c.scenario}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
