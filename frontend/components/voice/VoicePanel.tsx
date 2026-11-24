"use client";

import {
  Headphones,
  Info,
  Loader2,
  MessageSquare,
  Mic,
  MicOff,
  PhoneOff,
  RefreshCw,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";

import type { VoiceSession } from "@/hooks/useVoiceSession";
import type { EnginePreference, VoicePhase } from "@/hooks/voiceTypes";

import { Button } from "../ui/IconButton";
import { Waveform } from "./Waveform";

const PHASE_COPY: Record<VoicePhase, { label: string; tone: string }> = {
  idle: { label: "Voice off", tone: "bg-surface-3" },
  "requesting-mic": { label: "Waiting for microphone permission…", tone: "bg-warning" },
  connecting: { label: "Connecting to Nova…", tone: "bg-warning" },
  ready: { label: "Ready - start talking", tone: "bg-success" },
  listening: { label: "Listening…", tone: "bg-accent" },
  thinking: { label: "Working on it…", tone: "bg-violet" },
  speaking: { label: "Nova is speaking - talk to interrupt", tone: "bg-violet" },
  closed: { label: "Disconnected", tone: "bg-surface-3" },
  error: { label: "Voice unavailable", tone: "bg-danger" },
};

const ENGINES: { value: EnginePreference; label: string; hint: string }[] = [
  { value: "auto", label: "Auto", hint: "Realtime first, standard pipeline as fallback" },
  { value: "realtime", label: "Realtime", hint: "Gemini Live native audio (lowest latency)" },
  { value: "pipeline", label: "Pipeline", hint: "Browser speech-to-text → agent → text-to-speech" },
];

function EngineSelector({ voice }: { voice: VoiceSession }) {
  return (
    <div role="radiogroup" aria-label="Voice engine" className="flex rounded-lg border border-border bg-surface-2 p-0.5">
      {ENGINES.map((e) => (
        <button
          key={e.value}
          type="button"
          role="radio"
          aria-checked={voice.preference === e.value}
          title={e.hint}
          disabled={e.value === "pipeline" && !voice.pipelineAvailable}
          onClick={() => void voice.setPreference(e.value)}
          className={`rounded-md px-2 py-0.5 text-[11px] font-medium transition-colors disabled:opacity-40 ${
            voice.preference === e.value ? "bg-surface text-text shadow-card" : "text-muted hover:text-text"
          }`}
        >
          {e.label}
        </button>
      ))}
    </div>
  );
}

export function VoicePanel({ voice, onExit }: { voice: VoiceSession; onExit: () => void }) {
  const copy = PHASE_COPY[voice.phase];
  const busy = voice.phase === "requesting-mic" || voice.phase === "connecting";
  const exitToChat = () => {
    voice.clearError();
    void voice.stop();
    onExit();
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      <div className="rounded-2xl border border-border bg-surface p-3 shadow-card">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 px-1">
          <EngineSelector voice={voice} />
          <span className="text-[11px] text-subtle">
            Engine: <span className="font-medium text-muted">{voice.engine === "realtime" ? "Gemini Live" : "STT → agent → TTS"}</span>
          </span>
        </div>

        {voice.notice && (
          <div role="status" className="mb-2 flex items-start gap-2 rounded-lg bg-accent-soft px-2.5 py-1.5 text-xs text-accent">
            <Info className="mt-0.5 size-3.5 shrink-0" />
            <span className="flex-1">{voice.notice}</span>
            <button type="button" onClick={voice.dismissNotice} aria-label="Dismiss notice" className="opacity-70 hover:opacity-100">
              <X className="size-3.5" />
            </button>
          </div>
        )}

        {voice.error ? (
          <div role="alert" className="flex flex-col gap-3 rounded-xl bg-danger-soft p-3 text-sm text-danger sm:flex-row sm:items-center">
            {voice.error.kind === "mic-denied" ? <MicOff className="size-5 shrink-0" /> : <VolumeX className="size-5 shrink-0" />}
            <p className="flex-1">{voice.error.message}</p>
            <div className="flex flex-wrap gap-2">
              {voice.error.code === "live_unavailable" && voice.pipelineAvailable && (
                <Button size="sm" onClick={() => void voice.setPreference("pipeline", true)}>
                  <Mic className="size-3.5" /> Use standard voice
                </Button>
              )}
              {voice.error.retryable && voice.error.code !== "live_unavailable" && (
                <Button size="sm" onClick={() => void voice.reconnect()}>
                  <RefreshCw className="size-3.5" /> {voice.error.kind === "mic-denied" ? "Try again" : "Reconnect"}
                </Button>
              )}
              <Button size="sm" onClick={exitToChat}>
                <MessageSquare className="size-3.5" /> Continue in chat
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between gap-2 px-1 pb-2">
              <div className="flex items-center gap-2 text-sm" role="status" aria-live="polite">
                <span className="relative flex size-2.5">
                  {(voice.phase === "listening" || voice.phase === "speaking") && (
                    <span className={`absolute inline-flex size-full animate-ping rounded-full opacity-60 ${copy.tone}`} />
                  )}
                  <span className={`relative inline-flex size-2.5 rounded-full ${copy.tone}`} />
                </span>
                <span className="font-medium">{copy.label}</span>
                {busy && <Loader2 className="size-3.5 animate-spin text-muted" />}
              </div>
              <div className="flex items-center gap-3 text-[11px] text-muted">
                <span className="flex items-center gap-1">
                  <span className="inline-block h-0.5 w-3 rounded bg-[var(--wave-mic)]" /> You
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-0.5 w-3 rounded bg-[var(--wave-agent)]" /> Nova
                </span>
              </div>
            </div>
            <div className="overflow-hidden rounded-xl bg-surface-2">
              <Waveform mic={voice.micAnalyser} agent={voice.outAnalyser} />
            </div>
          </>
        )}

        <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
          {voice.active ? (
            <>
              <Button
                onClick={voice.toggleMute}
                aria-pressed={voice.muted}
                aria-label={voice.muted ? "Unmute microphone" : "Mute microphone"}
              >
                {voice.muted ? <MicOff className="size-4 text-danger" /> : <Mic className="size-4" />}
                {voice.muted ? "Unmute" : "Mute"}
              </Button>
              <Button onClick={voice.interrupt} disabled={voice.phase !== "speaking"} aria-label="Interrupt the agent">
                <VolumeX className="size-4" /> Interrupt
              </Button>
              <Button variant="danger" onClick={() => void voice.stop()} aria-label="End voice call">
                <PhoneOff className="size-4" /> End call
              </Button>
            </>
          ) : (
            !voice.error && (
              <Button variant="primary" onClick={() => void voice.start()} aria-label="Start voice call">
                <Volume2 className="size-4" /> Start voice call
              </Button>
            )
          )}
        </div>
        <p className="mt-2 flex items-center justify-center gap-1.5 text-center text-[11px] text-subtle">
          {voice.engine === "pipeline" && <Headphones className="size-3 shrink-0" />}
          {voice.engine === "pipeline"
            ? "Standard pipeline: headphones give the most reliable interruptions. Requires Chrome or Edge."
            : "Speak naturally - you can interrupt Nova at any time. Transcripts and tool steps appear in the conversation."}
        </p>
      </div>
    </div>
  );
}
