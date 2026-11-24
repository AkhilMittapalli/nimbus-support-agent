"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { startMicMeter, SyntheticVoiceLevel, type MicMeter } from "@/lib/audio";
import type { AgentEvent, ChaosConfig, Mode } from "@/lib/protocol";
import {
  getSpeechRecognition,
  looksLikeEcho,
  pipelineSupported,
  SpeechQueue,
  takeSentences,
  type SpeechRecognitionLike,
} from "@/lib/speech";

import type { TurnHandle } from "./useConversation";
import type { VoiceEngine, VoiceError, VoicePhase } from "./voiceTypes";

/** Mic RMS above this for BARGE_IN_MS while the agent speaks counts as an interruption. */
const BARGE_IN_RMS = 0.08;
const BARGE_IN_MS = 250;
const MONITOR_INTERVAL_MS = 50;

interface Options {
  chaos: ChaosConfig | null;
  sendVoice: (text: string, chaos: ChaosConfig | null, tap: (e: AgentEvent) => void) => TurnHandle | null;
  interruptTurn: () => boolean;
  markInterrupted: (assistantId: string) => void;
  onEvent: (event: AgentEvent, mode: Mode) => void;
  onClosed: () => void;
}

interface ActiveTurn {
  assistantId: string;
  streaming: boolean;
  buffer: string;
}

/**
 * Cascaded voice pipeline (fallback when the realtime Live API is unavailable):
 *   browser SpeechRecognition (STT) -> POST /api/chat mode="voice" (same agent, tools, guardrails, traces)
 *   -> streamed tokens spoken sentence-by-sentence with speechSynthesis (TTS).
 * Barge-in: sustained mic energy or non-echo recognised speech while the agent is talking cancels TTS,
 * aborts the in-flight turn and marks it interrupted; the new speech becomes the next turn.
 */
export function usePipelineEngine(opts: Options): VoiceEngine {
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [error, setError] = useState<VoiceError | null>(null);
  const [muted, setMuted] = useState(false);
  const [micAnalyser, setMicAnalyser] = useState<AnalyserNode | null>(null);
  const [outAnalyser, setOutAnalyser] = useState<AnalyserNode | null>(null);

  const optsRef = useRef(opts);
  useEffect(() => {
    optsRef.current = opts;
  }, [opts]);

  const activeRef = useRef(false);
  const mutedRef = useRef(false);
  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const meterRef = useRef<MicMeter | null>(null);
  const synthRef = useRef<SyntheticVoiceLevel | null>(null);
  const queueRef = useRef<SpeechQueue | null>(null);
  const turnRef = useRef<ActiveTurn | null>(null);
  const monitorRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const loudSinceRef = useRef<number | null>(null);
  const restartRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const agentBusy = () => Boolean(queueRef.current?.speaking || turnRef.current?.streaming);

  const idlePhase = useCallback((): VoicePhase => (turnRef.current?.streaming ? "thinking" : "ready"), []);

  /** Interrupt the agent: stop talking, abort the turn if still streaming, mark the message. */
  const bargeIn = useCallback(() => {
    const queue = queueRef.current;
    const turn = turnRef.current;
    const wasSpeaking = Boolean(queue?.speaking);
    if (!wasSpeaking && !turn?.streaming) return;
    queue?.cancel();
    loudSinceRef.current = null;
    if (turn) {
      if (turn.streaming) {
        optsRef.current.interruptTurn();
      } else if (wasSpeaking) {
        optsRef.current.markInterrupted(turn.assistantId);
      }
      turn.streaming = false;
      turn.buffer = "";
    }
    optsRef.current.onEvent({ type: "interrupted" }, "voice");
    setPhase("listening");
  }, []);

  const handleUtterance = useCallback(
    (text: string) => {
      if (agentBusy()) bargeIn();
      const { sendVoice, chaos } = optsRef.current;
      let handle: TurnHandle | null = null;
      const tap = (event: AgentEvent) => {
        const turn = turnRef.current;
        const queue = queueRef.current;
        if (!turn || !handle || turn.assistantId !== handle.assistantId || !queue) return;
        switch (event.type) {
          case "token": {
            const [sentences, rest] = takeSentences(turn.buffer + event.text);
            turn.buffer = rest;
            sentences.forEach((s) => queue.speak(s));
            break;
          }
          case "message.replace":
            queue.cancel();
            turn.buffer = "";
            takeSentences(`${event.text}\n`)[0].forEach((s) => queue.speak(s));
            break;
          case "message.done":
            if (turn.buffer.trim()) queue.speak(turn.buffer);
            turn.buffer = "";
            break;
          case "error":
            queue.speak("Sorry, I ran into a problem. Please try again in a moment.");
            break;
          default:
            break;
        }
      };
      handle = sendVoice(text, chaos, tap);
      if (!handle) return;
      turnRef.current = { assistantId: handle.assistantId, streaming: true, buffer: "" };
      setPhase("thinking");
      const mine = handle;
      void mine.done.then(() => {
        const turn = turnRef.current;
        // Only a turn that finished on its own returns to "ready"; a barge-in already moved on.
        if (turn && turn.assistantId === mine.assistantId && turn.streaming) {
          turn.streaming = false;
          if (!queueRef.current?.speaking && activeRef.current) setPhase("ready");
        }
      });
    },
    [bargeIn],
  );

  const startRecognition = useCallback(() => {
    const rec = recRef.current;
    if (!rec || !activeRef.current || mutedRef.current) return;
    try {
      rec.start();
    } catch {
      // already started
    }
  }, []);

  const teardown = useCallback(async () => {
    activeRef.current = false;
    if (restartRef.current) clearTimeout(restartRef.current);
    if (monitorRef.current) clearInterval(monitorRef.current);
    monitorRef.current = null;
    const rec = recRef.current;
    recRef.current = null;
    if (rec) {
      rec.onresult = null;
      rec.onend = null;
      rec.onerror = null;
      rec.onspeechstart = null;
      try {
        rec.abort();
      } catch {
        // ignore
      }
    }
    queueRef.current?.dispose();
    queueRef.current = null;
    if (turnRef.current?.streaming) optsRef.current.interruptTurn();
    turnRef.current = null;
    const meter = meterRef.current;
    const synth = synthRef.current;
    meterRef.current = null;
    synthRef.current = null;
    setMicAnalyser(null);
    setOutAnalyser(null);
    await Promise.allSettled([meter?.close(), synth?.close()]);
    optsRef.current.onClosed();
  }, []);

  const start = useCallback(async () => {
    if (activeRef.current) return;
    setError(null);
    if (!pipelineSupported()) {
      setError({
        kind: "unsupported",
        message:
          "Voice mode needs the Web Speech API, which this browser doesn't provide. Please use Chrome or Edge, or continue in chat.",
        retryable: false,
      });
      setPhase("error");
      return;
    }
    activeRef.current = true;
    mutedRef.current = false;
    setMuted(false);
    setPhase("requesting-mic");

    // Synthetic output level + TTS queue are created inside the click gesture (autoplay policy).
    const synth = new SyntheticVoiceLevel();
    synthRef.current = synth;
    await synth.resume();
    setOutAnalyser(synth.analyser);
    queueRef.current = new SpeechQueue((speaking) => {
      synthRef.current?.setActive(speaking);
      if (!activeRef.current) return;
      setPhase(speaking ? "speaking" : idlePhase());
    });

    try {
      meterRef.current = await startMicMeter();
    } catch (err) {
      const denied = err instanceof DOMException && (err.name === "NotAllowedError" || err.name === "SecurityError");
      setError({
        kind: denied ? "mic-denied" : "mic-unavailable",
        message: denied
          ? "Microphone access was blocked. Allow it in your browser's site settings, then try again."
          : "No usable microphone was found.",
        retryable: true,
      });
      await teardown();
      setPhase("error");
      return;
    }
    if (!activeRef.current) return;
    setMicAnalyser(meterRef.current.analyser);

    const Ctor = getSpeechRecognition();
    if (!Ctor) return;
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-US";
    rec.maxAlternatives = 1;
    recRef.current = rec;

    rec.onresult = (e) => {
      let interim = "";
      let final = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) final += r[0].transcript;
        else interim += r[0].transcript;
      }
      const spoken = queueRef.current?.recentText ?? "";
      const agentTalking = Boolean(queueRef.current?.speaking);
      if (interim.trim()) {
        // While the agent talks, ignore what is probably its own voice leaking into the mic.
        if (agentTalking && looksLikeEcho(interim, spoken)) return;
        if (agentBusy() && interim.trim().split(/\s+/).length >= 2) bargeIn();
        setPhase("listening");
        optsRef.current.onEvent({ type: "transcript", role: "user", text: interim.trim(), final: false }, "voice");
      }
      if (final.trim()) {
        if (agentTalking && looksLikeEcho(final, spoken)) return;
        handleUtterance(final.trim());
      }
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        setError({
          kind: "mic-denied",
          message: "Speech recognition was blocked. Allow microphone access for this site and try again.",
          retryable: true,
        });
        setPhase("error");
        void teardown();
      } else if (e.error === "network") {
        setError({
          kind: "connection",
          message: "The browser's speech recognition service is unreachable. Check your connection, or continue in chat.",
          retryable: true,
        });
        setPhase("error");
        void teardown();
      }
      // "no-speech" / "aborted" / "audio-capture" glitches: onend restarts recognition.
    };
    rec.onend = () => {
      // Chrome ends continuous recognition after silence; keep listening while the call is active.
      if (!activeRef.current || mutedRef.current) return;
      restartRef.current = setTimeout(startRecognition, 150);
    };

    // Energy-based barge-in: works even before the recogniser produces words.
    monitorRef.current = setInterval(() => {
      const meter = meterRef.current;
      if (!meter || mutedRef.current || !queueRef.current?.speaking) {
        loudSinceRef.current = null;
        return;
      }
      if (meter.level() > BARGE_IN_RMS) {
        loudSinceRef.current ??= performance.now();
        if (performance.now() - loudSinceRef.current > BARGE_IN_MS) bargeIn();
      } else {
        loudSinceRef.current = null;
      }
    }, MONITOR_INTERVAL_MS);

    startRecognition();
    setPhase("ready");
  }, [bargeIn, handleUtterance, idlePhase, startRecognition, teardown]);

  const stop = useCallback(async () => {
    await teardown();
    setPhase("idle");
    setMuted(false);
  }, [teardown]);

  const reconnect = useCallback(async () => {
    await teardown();
    await start();
  }, [teardown, start]);

  const toggleMute = useCallback(() => {
    const next = !mutedRef.current;
    mutedRef.current = next;
    setMuted(next);
    if (next) {
      try {
        recRef.current?.abort();
      } catch {
        // ignore
      }
    } else {
      startRecognition();
    }
  }, [startRecognition]);

  useEffect(() => {
    return () => {
      void teardown();
    };
  }, [teardown]);

  return {
    phase,
    error,
    muted,
    micAnalyser,
    outAnalyser,
    active: phase !== "idle" && phase !== "error",
    start,
    stop,
    reconnect,
    toggleMute,
    interrupt: bargeIn,
    clearError: () => setError(null),
  };
}
