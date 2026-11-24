import type { VoiceStateName } from "@/lib/protocol";

export type VoicePhase = "idle" | "requesting-mic" | VoiceStateName | "error";

export interface VoiceError {
  kind: "mic-denied" | "mic-unavailable" | "connection" | "server" | "unsupported";
  code?: string;
  message: string;
  retryable: boolean;
}

/** Common surface of both voice engines (realtime Gemini Live, cascaded pipeline). */
export interface VoiceEngine {
  phase: VoicePhase;
  error: VoiceError | null;
  muted: boolean;
  micAnalyser: AnalyserNode | null;
  outAnalyser: AnalyserNode | null;
  active: boolean;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  reconnect: () => Promise<void>;
  toggleMute: () => void;
  interrupt: () => void;
  clearError: () => void;
}

export type EngineName = "realtime" | "pipeline";
export type EnginePreference = "auto" | EngineName;
