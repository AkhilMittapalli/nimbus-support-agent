"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { ChaosConfig } from "@/lib/protocol";
import { pipelineSupported } from "@/lib/speech";
import { readStorage, writeStorage } from "@/lib/storage";

import type { Conversation } from "./useConversation";
import { usePipelineEngine } from "./usePipelineEngine";
import { useRealtimeEngine } from "./useRealtimeEngine";
import type { EngineName, EnginePreference, VoiceEngine } from "./voiceTypes";

const PREF_KEY = "nimbus.voice_engine";

function readPreference(): EnginePreference {
  const v = readStorage(PREF_KEY);
  return v === "realtime" || v === "pipeline" || v === "auto" ? v : "auto";
}

export interface VoiceSession extends VoiceEngine {
  engine: EngineName;
  preference: EnginePreference;
  setPreference: (p: EnginePreference, autoStart?: boolean) => Promise<void>;
  notice: string | null;
  dismissNotice: () => void;
  pipelineAvailable: boolean;
}

/**
 * One voice interface over two engines:
 *  - "realtime": Gemini Live over our WebSocket bridge (native audio, server VAD, barge-in).
 *  - "pipeline": browser STT -> agent turn (SSE, mode="voice") -> browser TTS.
 * In "auto", realtime is tried first and the session falls back to the pipeline when the backend
 * reports `live_unavailable`.
 */
export function useVoiceSession({
  conversation,
  chaos,
}: {
  conversation: Conversation;
  chaos: ChaosConfig | null;
}): VoiceSession {
  const [preference, setPreferenceState] = useState<EnginePreference>("auto");
  const [engine, setEngine] = useState<EngineName>("realtime");
  const [notice, setNotice] = useState<string | null>(null);
  const [pipelineAvailable, setPipelineAvailable] = useState(true);
  const preferenceRef = useRef<EnginePreference>("auto");
  const startPipelineRef = useRef<() => Promise<void>>(async () => {});

  // Restore the saved engine preference and detect Web Speech support after mount (client-only APIs).
  useEffect(() => {
    const pref = readPreference();
    preferenceRef.current = pref;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- syncing from browser-only storage/APIs
    setPreferenceState(pref);
    setEngine(pref === "pipeline" ? "pipeline" : "realtime");
    setPipelineAvailable(pipelineSupported());
  }, []);

  const onLiveUnavailable = useCallback(() => {
    if (preferenceRef.current !== "auto") return false;
    setEngine("pipeline");
    setNotice("Realtime voice is unavailable right now - using the standard voice pipeline instead.");
    void startPipelineRef.current();
    return true;
  }, []);

  const realtime = useRealtimeEngine({
    sessionId: conversation.sessionId,
    chaosTool: chaos?.tool_failure ?? null,
    onEvent: conversation.ingest,
    onClosed: conversation.voiceClosed,
    onLiveUnavailable,
  });

  const pipeline = usePipelineEngine({
    chaos,
    sendVoice: conversation.sendVoice,
    interruptTurn: conversation.interruptTurn,
    markInterrupted: conversation.markInterrupted,
    onEvent: conversation.ingest,
    onClosed: conversation.voiceClosed,
  });

  useEffect(() => {
    startPipelineRef.current = pipeline.start;
  }, [pipeline.start]);

  const setPreference = useCallback(
    async (p: EnginePreference, autoStart = false) => {
      await Promise.all([realtime.stop(), pipeline.stop()]);
      preferenceRef.current = p;
      writeStorage(PREF_KEY, p);
      setPreferenceState(p);
      setEngine(p === "pipeline" ? "pipeline" : "realtime");
      setNotice(null);
      if (autoStart) await (p === "pipeline" ? pipeline : realtime).start();
    },
    [realtime, pipeline],
  );

  const current = engine === "pipeline" ? pipeline : realtime;

  const start = useCallback(async () => {
    setNotice(null);
    // "auto" retries realtime on every new call (the Live API may have recovered).
    if (preferenceRef.current === "auto" && engine === "pipeline") {
      setEngine("realtime");
      await realtime.start();
      return;
    }
    await current.start();
  }, [current, engine, realtime]);

  const stop = useCallback(async () => {
    await Promise.all([realtime.stop(), pipeline.stop()]);
  }, [realtime, pipeline]);

  return {
    ...current,
    start,
    stop,
    engine,
    preference,
    setPreference,
    notice,
    dismissNotice: () => setNotice(null),
    pipelineAvailable,
  };
}
