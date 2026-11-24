/**
 * Event protocol streamed by the backend (SSE for chat, WebSocket JSON frames for voice).
 * Mirrors backend/app/agent/events.py - keep the two in sync.
 */

export type Mode = "text" | "voice";
export type RetrievalBand = "high" | "medium" | "low" | "none";
export type GuardrailVerdict = "grounded" | "ungrounded" | "skipped" | "error";
export type GuardrailAction = "answer" | "retracted" | "escalated" | "flagged";
export type StepStatus = "ok" | "error" | "blocked";
export type VoiceStateName = "connecting" | "ready" | "listening" | "thinking" | "speaking" | "closed";

export interface CustomerView {
  customer_id: string;
  name: string;
  email: string;
  company: string | null;
  tier: "free" | "starter" | "pro" | "enterprise";
  contract_status: string;
  contract_end: string | null;
  billing_email: string;
  notification_pref: string;
  phone: string | null;
}

export interface Ticket {
  id: string;
  customer_id: string | null;
  category: string;
  priority: "low" | "medium" | "high" | "urgent";
  reason: string;
  summary: string;
  status: string;
  created_at: string;
}

export interface GuardrailResult {
  retrieval_band: RetrievalBand;
  top_similarity: number | null;
  verdict: GuardrailVerdict;
  groundedness: number | null;
  confidence: number;
  action: GuardrailAction;
  reasons: string[];
}

export type SpanKind = "llm" | "tool" | "retrieval" | "guardrail" | "voice" | "system";
export type SpanStatus = "ok" | "error" | "retry" | "blocked";

export interface TraceSpan {
  id: string;
  name: string;
  kind: SpanKind;
  start_ms: number;
  duration_ms: number | null;
  status: SpanStatus;
  attrs: Record<string, unknown>;
}

export interface ModelUsage {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  audio_input_tokens: number;
  audio_output_tokens: number;
  cost_usd: number;
}

export interface TurnTrace {
  turn_id: string;
  session_id: string;
  mode: Mode;
  started_at: string;
  latency: { first_token_ms: number | null; total_ms: number | null };
  usage: Record<string, ModelUsage>;
  totals: ModelUsage;
  spans: TraceSpan[];
  outcome: Record<string, unknown>;
}

// ---- events -------------------------------------------------------------------------------
export interface TurnStartEvent {
  type: "turn.start";
  turn_id: string;
  mode: Mode;
}
export interface StatusEvent {
  type: "status";
  label: string;
  level: "info" | "warning";
}
export interface StepStartEvent {
  type: "step.start";
  step_id: string;
  tool: string;
  label: string;
  args: Record<string, unknown>;
}
export interface StepRetryEvent {
  type: "step.retry";
  step_id: string;
  tool: string;
  attempt: number;
  reason: string;
}
export interface StepEndEvent {
  type: "step.end";
  step_id: string;
  tool: string;
  status: StepStatus;
  duration_ms: number;
  summary: string;
}
export interface TokenEvent {
  type: "token";
  text: string;
}
export interface MessageReplaceEvent {
  type: "message.replace";
  text: string;
  reason: string;
}
export interface MessageDoneEvent {
  type: "message.done";
  text: string;
}
export interface CustomerEvent {
  type: "customer";
  customer: CustomerView | null;
}
export interface EscalationEvent {
  type: "escalation";
  ticket: Ticket;
}
export interface GuardrailEvent extends GuardrailResult {
  type: "guardrail";
}
export interface TraceEvent {
  type: "trace";
  trace: TurnTrace;
}
export interface ErrorEvent {
  type: "error";
  code: string;
  message: string;
  retryable: boolean;
}
export interface TurnEndEvent {
  type: "turn.end";
  turn_id: string;
}
export interface TranscriptEvent {
  type: "transcript";
  role: "user" | "assistant";
  text: string;
  final: boolean;
}
export interface InterruptedEvent {
  type: "interrupted";
}
export interface VoiceStateEvent {
  type: "voice.state";
  state: VoiceStateName;
  detail: string | null;
}

export type AgentEvent =
  | TurnStartEvent
  | StatusEvent
  | StepStartEvent
  | StepRetryEvent
  | StepEndEvent
  | TokenEvent
  | MessageReplaceEvent
  | MessageDoneEvent
  | CustomerEvent
  | EscalationEvent
  | GuardrailEvent
  | TraceEvent
  | ErrorEvent
  | TurnEndEvent
  | TranscriptEvent
  | InterruptedEvent
  | VoiceStateEvent;

const EVENT_TYPES: ReadonlySet<string> = new Set<AgentEvent["type"]>([
  "turn.start",
  "status",
  "step.start",
  "step.retry",
  "step.end",
  "token",
  "message.replace",
  "message.done",
  "customer",
  "escalation",
  "guardrail",
  "trace",
  "error",
  "turn.end",
  "transcript",
  "interrupted",
  "voice.state",
]);

/** Narrow an unknown JSON payload to an AgentEvent (shape is trusted once the discriminator matches). */
export function parseAgentEvent(value: unknown): AgentEvent | null {
  if (typeof value !== "object" || value === null) return null;
  const type = (value as { type?: unknown }).type;
  if (typeof type !== "string" || !EVENT_TYPES.has(type)) return null;
  return value as AgentEvent;
}

// ---- chaos (fault injection) -----------------------------------------------------------------
export type LLMFault = "none" | "rate_limit_once" | "timeout_once" | "outage";

export interface ChaosConfig {
  tool_failure: string | null;
  tool_failure_mode: "once" | "always";
  llm_fault: LLMFault;
}

export const NO_CHAOS: ChaosConfig = { tool_failure: null, tool_failure_mode: "once", llm_fault: "none" };

export const TOOL_NAMES = [
  "verify_customer",
  "get_account_overview",
  "search_knowledge_base",
  "issue_refund",
  "update_account",
  "create_escalation_ticket",
] as const;
