/**
 * Web Speech API helpers for the cascaded voice pipeline (browser STT -> agent -> browser TTS).
 * The Web Speech API isn't in TypeScript's DOM lib, so the minimal surface we use is typed here.
 */

export interface SpeechRecognitionAlternativeLike {
  transcript: string;
  confidence: number;
}

export interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]: SpeechRecognitionAlternativeLike;
}

export interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: { readonly length: number; [index: number]: SpeechRecognitionResultLike };
}

export interface SpeechRecognitionErrorEventLike {
  readonly error: string;
}

export interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  onresult: ((e: SpeechRecognitionEventLike) => void) | null;
  onerror: ((e: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  onspeechstart: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

export function getSpeechRecognition(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: SpeechRecognitionCtor; webkitSpeechRecognition?: SpeechRecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function pipelineSupported(): boolean {
  return getSpeechRecognition() !== null && typeof window !== "undefined" && "speechSynthesis" in window;
}

/** Strip markdown, citations and symbols that sound bad when read aloud. */
export function toSpeakable(text: string): string {
  return text
    .replace(/^\s*\(via voice\)\s*/i, "") // history marker the model sometimes echoes
    .replace(/\[[a-z0-9][a-z0-9-]*#[^\]\n]{1,80}\]/gi, "") // [refund-policy#Section] citations
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") // [text](url) -> text
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\*\*|__|\*|_|~~/g, "")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*(\d+)[.)]\s+/gm, "$1. ")
    .replace(/\|/g, " ")
    .replace(/\s+([.,;:!?])/g, "$1")
    .replace(/[ \t]+/g, " ")
    .trim();
}

/**
 * Split off complete sentences from a growing token buffer.
 * Returns [sentencesReadyToSpeak, remainder].
 */
export function takeSentences(buffer: string): [string[], string] {
  const out: string[] = [];
  let rest = buffer;
  const re = /([.!?])(\s+)|\n+/;
  for (;;) {
    const m = re.exec(rest);
    if (!m) break;
    const end = m.index + (m[1] ? 1 : 0);
    // Avoid splitting "e.g. " / "$1.50" style tokens: require some length before a period.
    const candidate = rest.slice(0, end).trim();
    rest = rest.slice(m.index + m[0].length);
    if (candidate) out.push(candidate);
  }
  return [out, rest];
}

function pickVoice(): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis.getVoices();
  const english = voices.filter((v) => v.lang.toLowerCase().startsWith("en"));
  const prefer = [/natural/i, /google us english/i, /samantha/i, /aria/i, /jenny/i, /google/i];
  for (const p of prefer) {
    const hit = english.find((v) => p.test(v.name));
    if (hit) return hit;
  }
  return english[0] ?? voices[0] ?? null;
}

/** Sentence-by-sentence TTS queue with a speaking-state callback and "what is being said" for echo checks. */
export class SpeechQueue {
  private pending = 0;
  private generation = 0;
  private spokenRecently = "";
  private voice: SpeechSynthesisVoice | null = null;

  constructor(private readonly onSpeakingChange: (speaking: boolean) => void) {
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      this.voice = pickVoice();
      window.speechSynthesis.onvoiceschanged = () => {
        this.voice = pickVoice();
      };
    }
  }

  get speaking(): boolean {
    return this.pending > 0;
  }

  /** Text spoken in the last few utterances, used to ignore the agent's own voice picked up by the mic. */
  get recentText(): string {
    return this.spokenRecently;
  }

  speak(raw: string): void {
    const text = toSpeakable(raw);
    if (!text) return;
    const gen = this.generation;
    const u = new SpeechSynthesisUtterance(text);
    if (this.voice) u.voice = this.voice;
    u.rate = 1.05;
    u.onstart = () => {
      if (gen !== this.generation) return;
      this.spokenRecently = `${this.spokenRecently} ${text}`.slice(-600);
    };
    const finish = () => {
      if (gen !== this.generation) return;
      this.pending = Math.max(0, this.pending - 1);
      if (this.pending === 0) this.onSpeakingChange(false);
    };
    u.onend = finish;
    u.onerror = finish;
    this.pending += 1;
    if (this.pending === 1) this.onSpeakingChange(true);
    window.speechSynthesis.speak(u);
  }

  /** Stop immediately and drop everything queued (barge-in). */
  cancel(): void {
    this.generation += 1;
    const wasSpeaking = this.pending > 0;
    this.pending = 0;
    window.speechSynthesis.cancel();
    if (wasSpeaking) this.onSpeakingChange(false);
  }

  dispose(): void {
    this.cancel();
    window.speechSynthesis.onvoiceschanged = null;
  }
}

const norm = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 1);

/** True if most words of `heard` appear in what the agent just said (mic picked up the speaker). */
export function looksLikeEcho(heard: string, spoken: string): boolean {
  const words = norm(heard);
  if (words.length === 0) return true;
  const spokenSet = new Set(norm(spoken));
  const overlap = words.filter((w) => spokenSet.has(w)).length;
  return overlap / words.length >= 0.6;
}
