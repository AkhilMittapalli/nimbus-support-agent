import { Headset, Sparkles } from "lucide-react";

export const STARTER_PROMPTS: { title: string; text: string }[] = [
  {
    title: "Refund a double charge",
    text: "Hi, I'm bob@brightlabs.dev, PIN 2222. I was double charged this month - can you refund my latest invoice?",
  },
  {
    title: "Refund over the tier limit",
    text: "I'm carol@example.com, PIN 3333. Please refund the full extra-storage invoice INV-9006, I never used it.",
  },
  {
    title: "Out-of-scope policy question",
    text: "Does Nimbus Cloud sign HIPAA BAAs for healthcare customers?",
  },
  {
    title: "Troubleshoot file sync",
    text: "My files stopped syncing on my Windows laptop since yesterday. What should I try?",
  },
  {
    title: "Past-due account refund",
    text: "This is dan@okafor.co, PIN 4444. I want a refund on INV-9007.",
  },
  {
    title: "Talk to a human",
    text: "I'd like to speak to a human about my enterprise contract renewal.",
  },
];

export function EmptyState({ onPick, disabled }: { onPick: (text: string) => void; disabled: boolean }) {
  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center px-4 py-10 text-center sm:py-16">
      <div className="mb-4 flex size-12 items-center justify-center rounded-2xl bg-accent-soft text-accent">
        <Headset className="size-6" />
      </div>
      <h1 className="text-xl font-semibold tracking-tight">Hi, I&apos;m Nova from Nimbus Cloud support</h1>
      <p className="mt-2 max-w-md text-sm text-muted">
        Ask about billing, refunds, plans or troubleshooting - by chat or voice. I verify your identity, check your
        tier and policies, and take action within policy limits.
      </p>
      <div className="mt-8 grid w-full gap-2 sm:grid-cols-2">
        {STARTER_PROMPTS.map((p) => (
          <button
            key={p.title}
            type="button"
            disabled={disabled}
            onClick={() => onPick(p.text)}
            className="group rounded-xl border border-border bg-surface p-3 text-left shadow-card transition-colors hover:border-accent/50 hover:bg-accent-soft/40 disabled:opacity-50"
          >
            <span className="flex items-center gap-1.5 text-sm font-medium">
              <Sparkles className="size-3.5 text-accent" /> {p.title}
            </span>
            <span className="mt-1 line-clamp-2 block text-xs text-muted">{p.text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
