"use client";

import { Activity, AlertTriangle, Cloud, Keyboard, Loader2, Mic, Plus, RefreshCw } from "lucide-react";
import { useCallback, useState } from "react";

import { useConversation } from "@/hooks/useConversation";
import { useVoiceSession } from "@/hooks/useVoiceSession";
import { NO_CHAOS, type ChaosConfig } from "@/lib/protocol";

import { Composer } from "./chat/Composer";
import { EmptyState } from "./chat/EmptyState";
import { Timeline } from "./chat/Timeline";
import { ObservabilityDrawer } from "./observability/ObservabilityDrawer";
import { CustomerCard } from "./session/CustomerCard";
import { DemoAccounts } from "./session/DemoAccounts";
import { Button } from "./ui/IconButton";
import { VoicePanel } from "./voice/VoicePanel";

type InputMode = "chat" | "voice";

export function SupportConsole() {
  const conv = useConversation();
  const { state } = conv;
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<InputMode>("chat");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedTurn, setSelectedTurn] = useState<string | null>(null);
  const [chaos, setChaos] = useState<ChaosConfig>(NO_CHAOS);

  const chaosOrNull = chaos.tool_failure || chaos.llm_fault !== "none" ? chaos : null;

  const voice = useVoiceSession({ conversation: conv, chaos: chaosOrNull });

  const send = useCallback(
    (text: string) => {
      if (!text.trim()) return;
      setInput("");
      void conv.send(text, chaosOrNull);
    },
    [conv, chaosOrNull],
  );

  const switchMode = async (next: InputMode) => {
    if (next === mode) return;
    if (next === "chat") await voice.stop();
    setMode(next);
  };

  const newConversation = async () => {
    await voice.stop();
    setSelectedTurn(null);
    await conv.startNewSession();
  };

  const showTrace = (turnId: string) => {
    setSelectedTurn(turnId);
    setDrawerOpen(true);
  };

  const ready = conv.sessionStatus === "ready";
  const empty = state.items.length === 0;

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border bg-surface px-3 sm:px-4">
          <div className="flex shrink-0 items-center gap-2 whitespace-nowrap">
            <span className="flex size-8 items-center justify-center rounded-lg bg-accent text-accent-fg">
              <Cloud className="size-4" />
            </span>
            <div className="hidden leading-tight sm:block">
              <div className="text-sm font-semibold">Nimbus Support</div>
              <div className="hidden text-[11px] text-subtle sm:block">AI agent · chat & voice</div>
            </div>
          </div>

          <div
            role="radiogroup"
            aria-label="Conversation mode"
            className="flex rounded-lg border border-border bg-surface-2 p-0.5 sm:ml-4"
          >
            {(["chat", "voice"] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={mode === m}
                disabled={!ready || (m === "voice" && state.busy)}
                onClick={() => void switchMode(m)}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50 ${
                  mode === m ? "bg-surface text-text shadow-card" : "text-muted hover:text-text"
                }`}
              >
                {m === "chat" ? <Keyboard className="size-3.5" /> : <Mic className="size-3.5" />}
                <span className="capitalize">{m}</span>
              </button>
            ))}
          </div>

          <div className="ml-auto flex items-center gap-1 whitespace-nowrap">
            <DemoAccounts
              onInsert={(t) => {
                if (mode !== "chat") void switchMode("chat");
                setInput((prev) => (prev ? `${prev} ${t}` : t));
              }}
            />
            <Button size="sm" variant="ghost" onClick={() => void newConversation()} aria-label="New conversation">
              <Plus className="size-4" /> <span className="hidden sm:inline">New</span>
            </Button>
            <Button
              size="sm"
              variant={drawerOpen ? "outline" : "ghost"}
              onClick={() => setDrawerOpen((o) => !o)}
              aria-pressed={drawerOpen}
              aria-label="Toggle observability panel"
            >
              <Activity className="size-4" />
              <span className="hidden xl:inline">Observability</span>
              {state.traces.length > 0 && (
                <span className="rounded-full bg-accent-soft px-1.5 text-[10px] text-accent tabular-nums">
                  {state.traces.length}
                </span>
              )}
            </Button>
          </div>
        </header>

        <CustomerCard customer={state.customer} tickets={state.tickets} />

        <main className="min-h-0 flex-1">
          {conv.sessionStatus === "loading" ? (
            <div className="flex h-full items-center justify-center text-sm text-muted">
              <Loader2 className="mr-2 size-4 animate-spin" /> Loading conversation…
            </div>
          ) : conv.sessionStatus === "error" ? (
            <div role="alert" className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
              <AlertTriangle className="size-6 text-danger" />
              <p className="max-w-sm text-sm text-muted">{conv.sessionError}</p>
              <Button onClick={() => void conv.reload()}>
                <RefreshCw className="size-4" /> Retry
              </Button>
            </div>
          ) : empty ? (
            <div className="scrollbar-thin h-full overflow-y-auto">
              <EmptyState
                disabled={state.busy}
                onPick={(text) => {
                  if (mode === "chat") send(text);
                  else setInput(text);
                }}
              />
            </div>
          ) : (
            <Timeline
              items={state.items}
              onRetry={(id) => conv.retry(id, chaosOrNull)}
              onShowTrace={showTrace}
            />
          )}
        </main>

        <div className="shrink-0 pt-2">
          {mode === "chat" ? (
            <Composer
              value={input}
              onChange={setInput}
              onSend={() => send(input)}
              onStop={conv.stop}
              busy={state.busy}
              disabled={!ready}
            />
          ) : (
            <VoicePanel voice={voice} onExit={() => setMode("chat")} />
          )}
        </div>
      </div>

      <ObservabilityDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        traces={state.traces}
        selectedTurn={selectedTurn}
        onSelectTurn={setSelectedTurn}
        chaos={chaos}
        onChaosChange={setChaos}
        onDemoReset={() => void newConversation()}
      />
    </div>
  );
}
