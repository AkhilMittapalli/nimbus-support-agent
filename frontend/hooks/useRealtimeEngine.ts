"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { PcmPlayer, startMicCapture, type MicCapture } from "@/lib/audio";
import { WS_URL } from "@/lib/config";
import { parseAgentEvent, type AgentEvent, type Mode } from "@/lib/protocol";

import type { VoiceEngine, VoiceError, VoicePhase } from "./voiceTypes";

interface Options {
  sessionId: string | null;
  chaosTool: string | null;
  onEvent: (event: AgentEvent, mode: Mode) => void;
  onClosed: () => void;
  /** Return true to take over (e.g. auto-fallback to the pipeline engine) instead of showing an error. */
  onLiveUnavailable: () => boolean;
}

/**
 * Realtime engine - live voice session: mic -> WebSocket (PCM16 16 kHz) -> backend -> Gemini Live, and
 * model audio (PCM16 24 kHz) -> scheduled playback. Handles barge-in by flushing playback on
 * `interrupted`, and feeds every JSON event into the shared conversation timeline.
 */
export function useRealtimeEngine({ sessionId, chaosTool, onEvent, onClosed, onLiveUnavailable }: Options): VoiceEngine {
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [error, setError] = useState<VoiceError | null>(null);
  const [muted, setMutedState] = useState(false);
  const [micAnalyser, setMicAnalyser] = useState<AnalyserNode | null>(null);
  const [outAnalyser, setOutAnalyser] = useState<AnalyserNode | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const micRef = useRef<MicCapture | null>(null);
  const playerRef = useRef<PcmPlayer | null>(null);
  const activeRef = useRef(false);
  const onEventRef = useRef(onEvent);
  const onClosedRef = useRef(onClosed);
  const onLiveUnavailableRef = useRef(onLiveUnavailable);

  useEffect(() => {
    onEventRef.current = onEvent;
    onClosedRef.current = onClosed;
    onLiveUnavailableRef.current = onLiveUnavailable;
  }, [onEvent, onClosed, onLiveUnavailable]);

  const teardown = useCallback(async () => {
    activeRef.current = false;
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      ws.onmessage = null;
      ws.onclose = null;
      ws.onerror = null;
      if (ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ type: "end" }));
        } catch {
          // ignore
        }
      }
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.close(1000);
    }
    const mic = micRef.current;
    micRef.current = null;
    const player = playerRef.current;
    playerRef.current = null;
    setMicAnalyser(null);
    setOutAnalyser(null);
    await Promise.allSettled([mic?.close(), player?.close()]);
    onClosedRef.current();
  }, []);

  const stop = useCallback(async () => {
    await teardown();
    setPhase("idle");
    setMutedState(false);
  }, [teardown]);

  const start = useCallback(async () => {
    if (!sessionId || activeRef.current) return;
    activeRef.current = true;
    setError(null);
    setMutedState(false);
    setPhase("requesting-mic");

    // Create the playback context inside the click handler so autoplay policies allow it.
    const player = new PcmPlayer(24_000);
    playerRef.current = player;
    await player.resume();
    setOutAnalyser(player.analyser);

    let mic: MicCapture;
    try {
      mic = await startMicCapture((frame) => {
        const ws = wsRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(frame);
      });
    } catch (err) {
      const denied = err instanceof DOMException && (err.name === "NotAllowedError" || err.name === "SecurityError");
      setError({
        kind: denied ? "mic-denied" : "mic-unavailable",
        message: denied
          ? "Microphone access was blocked. Allow it in your browser's site settings, then try again."
          : "No usable microphone was found.",
        retryable: true,
      });
      setPhase("error");
      await teardown();
      return;
    }
    if (!activeRef.current) {
      await mic.close();
      return;
    }
    micRef.current = mic;
    setMicAnalyser(mic.analyser);
    setPhase("connecting");

    const params = new URLSearchParams({ session_id: sessionId });
    if (chaosTool) params.set("chaos_tool", chaosTool);
    const ws = new WebSocket(`${WS_URL}/ws/voice?${params.toString()}`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    player.setIdleListener(() => {
      setPhase((p) => (p === "speaking" ? "ready" : p));
    });

    ws.onmessage = (msg: MessageEvent<ArrayBuffer | string>) => {
      if (msg.data instanceof ArrayBuffer) {
        playerRef.current?.enqueue(msg.data);
        return;
      }
      let parsed: unknown;
      try {
        parsed = JSON.parse(msg.data);
      } catch {
        return;
      }
      const event = parseAgentEvent(parsed);
      if (!event) return;
      if (event.type === "interrupted") {
        playerRef.current?.flush();
        setPhase("listening");
      } else if (event.type === "voice.state") {
        if (event.state !== "closed") setPhase(event.state);
      } else if (event.type === "error") {
        if (event.code === "live_unavailable") {
          // The Live API refused the session before it started: let the facade fall back.
          void teardown().then(() => {
            if (!onLiveUnavailableRef.current()) {
              setError({ kind: "server", code: event.code, message: event.message, retryable: true });
              setPhase("error");
            } else {
              setPhase("idle");
            }
          });
          return;
        }
        // Errors are shown in the voice panel banner, not attached to a (possibly older) message.
        setError({ kind: "server", code: event.code, message: event.message, retryable: event.retryable });
        return;
      }
      onEventRef.current(event, "voice");
    };
    ws.onerror = () => {
      setError({ kind: "connection", message: "Couldn't connect to the voice service.", retryable: true });
    };
    ws.onclose = (e: CloseEvent) => {
      const unexpected = activeRef.current;
      void teardown();
      if (unexpected) {
        setError((prev) =>
          prev ?? {
            kind: "connection",
            message:
              e.code === 4429
                ? "Too many voice sessions - please wait a moment."
                : "The voice connection closed. Your conversation is saved.",
            retryable: true,
          },
        );
        setPhase("error");
      } else {
        setPhase("idle");
      }
    };
  }, [sessionId, chaosTool, teardown]);

  const reconnect = useCallback(async () => {
    await teardown();
    await start();
  }, [teardown, start]);

  const toggleMute = useCallback(() => {
    setMutedState((m) => {
      micRef.current?.setMuted(!m);
      return !m;
    });
  }, []);

  /** Manual barge-in (e.g. a "stop talking" button). */
  const interrupt = useCallback(() => {
    playerRef.current?.flush();
  }, []);

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
    interrupt,
    clearError: () => setError(null),
  };
}
