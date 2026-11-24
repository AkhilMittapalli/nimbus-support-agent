"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { api } from "@/lib/api";
import { conversationReducer, initialConversation, newId } from "@/lib/conversation";
import { ApiError } from "@/lib/errors";
import type { AgentEvent, ChaosConfig, Mode } from "@/lib/protocol";
import { postSSE } from "@/lib/sse";
import { readStorage, writeStorage } from "@/lib/storage";

const SESSION_KEY = "nimbus.session_id";

export type SessionStatus = "loading" | "ready" | "error";

export interface TurnHandle {
  assistantId: string;
  /** Resolves when the stream ends (completed, failed, stopped or interrupted). */
  done: Promise<void>;
}

interface InFlight {
  id: string;
  controller: AbortController;
  reason: "stop" | "interrupt" | null;
}

/**
 * Owns the session id, the unified conversation timeline (chat + voice) and chat-turn streaming.
 * The realtime voice engine feeds the same timeline through `ingest(event, "voice")`; the pipeline
 * voice engine runs normal agent turns with `mode: "voice"` through `sendVoice`.
 */
export function useConversation() {
  const [state, dispatch] = useReducer(conversationReducer, initialConversation);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionStatus, setSessionStatus] = useState<SessionStatus>("loading");
  const [sessionError, setSessionError] = useState<string | null>(null);
  const inFlightRef = useRef<InFlight | null>(null);
  const loadingRef = useRef<Promise<void> | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  const startNewSession = useCallback(async () => {
    inFlightRef.current?.controller.abort();
    setSessionStatus("loading");
    setSessionError(null);
    try {
      const { session_id } = await api.createSession();
      writeStorage(SESSION_KEY, session_id);
      dispatch({ type: "clear" });
      setSessionId(session_id);
      setSessionStatus("ready");
    } catch (err) {
      setSessionError(err instanceof ApiError ? err.message : "Could not start a session.");
      setSessionStatus("error");
    }
  }, []);

  const loadSessionOnce = useCallback(async () => {
    setSessionStatus("loading");
    setSessionError(null);
    const stored = readStorage(SESSION_KEY);
    if (!stored) {
      await startNewSession();
      return;
    }
    try {
      const [snapshot, { traces }] = await Promise.all([api.getSession(stored), api.getTraces(stored)]);
      dispatch({ type: "hydrate", snapshot, traces });
      setSessionId(stored);
      setSessionStatus("ready");
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        await startNewSession();
        return;
      }
      setSessionError(err instanceof ApiError ? err.message : "Could not load your conversation.");
      setSessionStatus("error");
    }
  }, [startNewSession]);

  // De-duplicates concurrent loads (React Strict Mode runs mount effects twice in development).
  const loadSession = useCallback(() => {
    if (!loadingRef.current) {
      loadingRef.current = loadSessionOnce().finally(() => {
        loadingRef.current = null;
      });
    }
    return loadingRef.current;
  }, [loadSessionOnce]);

  useEffect(() => {
    void loadSession();
    return () => inFlightRef.current?.controller.abort();
  }, [loadSession]);

  const ingest = useCallback((event: AgentEvent, mode: Mode) => {
    dispatch({ type: "ingest", event, mode });
  }, []);

  /** Run one agent turn over SSE. Returns null if no session or a turn is already in flight. */
  const runTurn = useCallback(
    (text: string, chaos: ChaosConfig | null, mode: Mode, tap?: (e: AgentEvent) => void): TurnHandle | null => {
      const message = text.trim();
      const sid = sessionIdRef.current;
      if (!message || !sid || inFlightRef.current) return null;
      const assistantId = newId("a");
      const turn: InFlight = { id: assistantId, controller: new AbortController(), reason: null };
      inFlightRef.current = turn;
      dispatch({ type: "chat.begin", userId: newId("u"), assistantId, text: message, mode });

      const done = (async () => {
        try {
          await postSSE(
            api.chatUrl,
            { session_id: sid, message, chaos, mode },
            (event) => {
              dispatch({ type: "ingest", event, mode: "text" });
              tap?.(event);
            },
            turn.controller.signal,
          );
          dispatch({ type: "chat.closed", id: assistantId });
        } catch (err) {
          if (turn.controller.signal.aborted) {
            dispatch({ type: turn.reason === "interrupt" ? "chat.interrupted" : "chat.stopped", id: assistantId });
          } else {
            const e = err instanceof ApiError ? err : null;
            const error = {
              code: e?.code ?? "network_error",
              message: e?.message ?? "Something went wrong. Please retry.",
              retryable: e?.retryable ?? true,
            };
            dispatch({ type: "chat.failed", id: assistantId, error });
            tap?.({ type: "error", ...error });
          }
        } finally {
          if (inFlightRef.current === turn) inFlightRef.current = null;
        }
      })();
      return { assistantId, done };
    },
    [],
  );

  const send = useCallback(
    (text: string, chaos: ChaosConfig | null) => runTurn(text, chaos, "text")?.done ?? Promise.resolve(),
    [runTurn],
  );

  const sendVoice = useCallback(
    (text: string, chaos: ChaosConfig | null, tap: (e: AgentEvent) => void) => runTurn(text, chaos, "voice", tap),
    [runTurn],
  );

  const abortTurn = useCallback((reason: "stop" | "interrupt") => {
    const turn = inFlightRef.current;
    if (!turn) return false;
    turn.reason = reason;
    turn.controller.abort();
    inFlightRef.current = null;
    return true;
  }, []);

  const stop = useCallback(() => void abortTurn("stop"), [abortTurn]);
  /** Barge-in for the voice pipeline: abort the streaming turn and mark it interrupted. */
  const interruptTurn = useCallback(() => abortTurn("interrupt"), [abortTurn]);
  const markInterrupted = useCallback((id: string) => dispatch({ type: "assistant.interrupted", id }), []);

  /** Remove a failed exchange and resend the same user text. */
  const retry = useCallback(
    (assistantId: string, chaos: ChaosConfig | null) => {
      const assistant = state.items.find((it) => it.id === assistantId);
      if (!assistant || assistant.kind !== "assistant" || !assistant.userId) return;
      const user = state.items.find((it) => it.id === assistant.userId);
      if (!user || user.kind !== "user") return;
      dispatch({ type: "remove", ids: [assistant.id, user.id] });
      void send(user.text, chaos);
    },
    [state.items, send],
  );

  const voiceClosed = useCallback(() => dispatch({ type: "voice.closed" }), []);

  return {
    state,
    sessionId,
    sessionStatus,
    sessionError,
    send,
    sendVoice,
    stop,
    interruptTurn,
    markInterrupted,
    retry,
    ingest,
    voiceClosed,
    startNewSession,
    reload: loadSession,
  };
}

export type Conversation = ReturnType<typeof useConversation>;
