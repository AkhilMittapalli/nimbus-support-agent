/**
 * Pure reducer for the unified conversation timeline. Chat (SSE) and voice (WebSocket) events both
 * flow through `ingest`, so a session that switches modes renders as one continuous conversation.
 */
import type { SessionSnapshot } from "./api";
import type {
  AgentEvent,
  CustomerView,
  GuardrailResult,
  Mode,
  StepStatus,
  Ticket,
  TurnTrace,
} from "./protocol";

export interface Step {
  id: string;
  tool: string;
  label: string;
  args: Record<string, unknown>;
  status: "running" | StepStatus;
  durationMs: number | null;
  summary: string | null;
  retries: string[];
}

export interface TurnError {
  code: string;
  message: string;
  retryable: boolean;
}

export interface UserItem {
  kind: "user";
  id: string;
  text: string;
  mode: Mode;
  live: boolean; // voice transcript still being recognised
}

export interface AssistantItem {
  kind: "assistant";
  id: string;
  userId: string | null; // the user message this answers (for retry)
  turnId: string | null;
  mode: Mode;
  text: string;
  streaming: boolean;
  status: string | null;
  notices: string[];
  steps: Step[];
  guardrail: GuardrailResult | null;
  replacedReason: string | null;
  tickets: Ticket[];
  error: TurnError | null;
  interrupted: boolean;
  stopped: boolean;
}

export type TimelineItem = UserItem | AssistantItem;

export interface ConversationState {
  items: TimelineItem[];
  customer: CustomerView | null;
  tickets: Ticket[];
  traces: TurnTrace[];
  /** Chat turn in flight. */
  busy: boolean;
  /** Assistant item receiving chat events. */
  activeChatId: string | null;
  /** Voice: items currently receiving live transcripts. */
  voiceUserId: string | null;
  voiceAssistantId: string | null;
  /** Voice: last assistant item (guardrail/trace arrive after transcripts finalise). */
  lastVoiceAssistantId: string | null;
  voiceTurnId: string | null;
}

export const initialConversation: ConversationState = {
  items: [],
  customer: null,
  tickets: [],
  traces: [],
  busy: false,
  activeChatId: null,
  voiceUserId: null,
  voiceAssistantId: null,
  lastVoiceAssistantId: null,
  voiceTurnId: null,
};

export type ConversationAction =
  | { type: "hydrate"; snapshot: SessionSnapshot; traces: TurnTrace[] }
  | { type: "clear" }
  | { type: "chat.begin"; userId: string; assistantId: string; text: string; mode: Mode }
  | { type: "chat.stopped"; id: string }
  | { type: "chat.interrupted"; id: string }
  | { type: "chat.failed"; id: string; error: TurnError }
  | { type: "chat.closed"; id: string }
  | { type: "assistant.interrupted"; id: string }
  | { type: "remove"; ids: string[] }
  | { type: "ingest"; event: AgentEvent; mode: Mode }
  | { type: "voice.closed" };

let counter = 0;
export function newId(prefix: string): string {
  counter += 1;
  return `${prefix}-${Date.now().toString(36)}-${counter}`;
}

function emptyAssistant(id: string, mode: Mode, userId: string | null): AssistantItem {
  return {
    kind: "assistant",
    id,
    userId,
    turnId: null,
    mode,
    text: "",
    streaming: true,
    status: null,
    notices: [],
    steps: [],
    guardrail: null,
    replacedReason: null,
    tickets: [],
    error: null,
    interrupted: false,
    stopped: false,
  };
}

function updateAssistant(
  state: ConversationState,
  id: string | null,
  fn: (a: AssistantItem) => AssistantItem,
): ConversationState {
  if (!id) return state;
  return {
    ...state,
    items: state.items.map((it) => (it.kind === "assistant" && it.id === id ? fn(it) : it)),
  };
}

function upsertTrace(traces: TurnTrace[], trace: TurnTrace): TurnTrace[] {
  const idx = traces.findIndex((t) => t.turn_id === trace.turn_id);
  if (idx === -1) return [...traces, trace];
  const next = traces.slice();
  next[idx] = trace;
  return next;
}

function upsertTicket(tickets: Ticket[], ticket: Ticket): Ticket[] {
  return tickets.some((t) => t.id === ticket.id) ? tickets : [...tickets, ticket];
}

/** Apply step/status/escalation/guardrail-type events common to chat and voice. */
function applyToAssistant(a: AssistantItem, event: AgentEvent): AssistantItem {
  switch (event.type) {
    case "turn.start":
      return { ...a, turnId: event.turn_id };
    case "status":
      return event.level === "warning"
        ? { ...a, status: event.label, notices: [...a.notices, event.label] }
        : { ...a, status: event.label };
    case "step.start":
      return {
        ...a,
        status: event.label,
        steps: [
          ...a.steps,
          {
            id: event.step_id,
            tool: event.tool,
            label: event.label,
            args: event.args,
            status: "running",
            durationMs: null,
            summary: null,
            retries: [],
          },
        ],
      };
    case "step.retry":
      return {
        ...a,
        steps: a.steps.map((s) =>
          s.id === event.step_id ? { ...s, retries: [...s.retries, `attempt ${event.attempt + 1}: ${event.reason}`] } : s,
        ),
      };
    case "step.end":
      return {
        ...a,
        steps: a.steps.map((s) =>
          s.id === event.step_id
            ? { ...s, status: event.status, durationMs: event.duration_ms, summary: event.summary }
            : s,
        ),
      };
    case "token":
      return { ...a, text: a.text + event.text, status: null };
    case "message.replace":
      return { ...a, text: event.text, replacedReason: event.reason };
    case "message.done":
      return { ...a, text: event.text };
    case "escalation":
      return { ...a, tickets: upsertTicket(a.tickets, event.ticket) };
    case "guardrail": {
      const { type: _type, ...result } = event;
      void _type;
      return { ...a, guardrail: result };
    }
    case "error":
      return {
        ...a,
        streaming: false,
        status: null,
        error: { code: event.code, message: event.message, retryable: event.retryable },
      };
    case "turn.end":
      return { ...a, streaming: false, status: null };
    default:
      return a;
  }
}

function ingestChat(state: ConversationState, event: AgentEvent): ConversationState {
  let next = updateAssistant(state, state.activeChatId, (a) => applyToAssistant(a, event));
  if (event.type === "turn.end") next = { ...next, busy: false, activeChatId: null };
  return next;
}

function ensureVoiceAssistant(state: ConversationState): [ConversationState, string] {
  if (state.voiceAssistantId) return [state, state.voiceAssistantId];
  const id = newId("va");
  const item = emptyAssistant(id, "voice", state.voiceUserId);
  item.turnId = state.voiceTurnId;
  return [
    { ...state, items: [...state.items, item], voiceAssistantId: id, lastVoiceAssistantId: id },
    id,
  ];
}

function isEmptyTranscript(text: string): boolean {
  const t = text.replace("[interrupted]", "").trim();
  return t.length === 0;
}

function ingestVoice(state: ConversationState, event: AgentEvent): ConversationState {
  switch (event.type) {
    case "turn.start":
      return { ...state, voiceTurnId: event.turn_id };
    case "transcript": {
      if (event.role === "user") {
        if (!state.voiceUserId) {
          if (isEmptyTranscript(event.text)) return state;
          const id = newId("vu");
          const item: UserItem = { kind: "user", id, text: event.text, mode: "voice", live: !event.final };
          return { ...state, items: [...state.items, item], voiceUserId: event.final ? null : id };
        }
        const id = state.voiceUserId;
        return {
          ...state,
          items: state.items.map((it) =>
            it.kind === "user" && it.id === id ? { ...it, text: event.text || it.text, live: !event.final } : it,
          ),
          voiceUserId: event.final ? null : id,
        };
      }
      // assistant transcript
      if (!state.voiceAssistantId && isEmptyTranscript(event.text)) return state;
      const [withItem, id] = ensureVoiceAssistant(state);
      const interrupted = event.text.endsWith("[interrupted]");
      const text = event.text.replace(/\s*\[interrupted\]$/, "");
      const updated = updateAssistant(withItem, id, (a) => ({
        ...a,
        text: text || a.text,
        interrupted: a.interrupted || interrupted,
        streaming: !event.final,
        status: null,
      }));
      return event.final ? { ...updated, voiceAssistantId: null } : updated;
    }
    case "interrupted": {
      const id = state.voiceAssistantId ?? state.lastVoiceAssistantId;
      return updateAssistant(state, id, (a) => ({ ...a, interrupted: true }));
    }
    case "step.start":
    case "step.retry":
    case "step.end":
    case "status":
    case "escalation": {
      const [withItem, id] = ensureVoiceAssistant(state);
      return updateAssistant(withItem, id, (a) => applyToAssistant(a, event));
    }
    case "guardrail":
    case "error": {
      const id = state.voiceAssistantId ?? state.lastVoiceAssistantId;
      return updateAssistant(state, id, (a) => applyToAssistant(a, event));
    }
    case "turn.end":
      return {
        ...updateAssistant(state, state.lastVoiceAssistantId, (a) => ({
          ...a,
          streaming: false,
          status: null,
          turnId: a.turnId ?? event.turn_id,
        })),
        voiceTurnId: null,
      };
    default:
      return state;
  }
}

export function conversationReducer(state: ConversationState, action: ConversationAction): ConversationState {
  switch (action.type) {
    case "clear":
      return initialConversation;
    case "hydrate": {
      const { snapshot, traces } = action;
      const ticketById = new Map(snapshot.tickets.map((t) => [t.id, t]));
      const items: TimelineItem[] = [];
      let lastUser: string | null = null;
      for (const m of snapshot.messages) {
        if (m.role === "user") {
          const id = `m-${m.id}`;
          lastUser = id;
          items.push({ kind: "user", id, text: m.content, mode: m.mode, live: false });
        } else {
          const interrupted = Boolean(m.meta.interrupted) || m.content.endsWith("[interrupted]");
          items.push({
            ...emptyAssistant(`m-${m.id}`, m.mode, lastUser),
            turnId: m.turn_id,
            text: m.content.replace(/\s*\[interrupted\]$/, ""),
            streaming: false,
            guardrail: m.meta.guardrail ?? null,
            steps: (m.meta.steps ?? []).map((st) => ({
              id: st.id,
              tool: st.tool,
              label: st.label,
              args: {},
              status: st.status,
              durationMs: st.duration_ms,
              summary: st.summary,
              retries: st.retries,
            })),
            tickets: (m.meta.tickets ?? []).flatMap((id) => {
              const t = ticketById.get(id);
              return t ? [t] : [];
            }),
            interrupted,
          });
        }
      }
      return {
        ...initialConversation,
        items,
        customer: snapshot.customer,
        tickets: snapshot.tickets,
        traces,
      };
    }
    case "chat.begin": {
      // Voice pipeline: promote the live interim-transcript bubble instead of adding a duplicate.
      const promote = action.mode === "voice" ? state.voiceUserId : null;
      const userId = promote ?? action.userId;
      const items: TimelineItem[] = promote
        ? state.items.map((it) =>
            it.kind === "user" && it.id === promote ? { ...it, text: action.text, live: false } : it,
          )
        : [...state.items, { kind: "user", id: userId, text: action.text, mode: action.mode, live: false }];
      const assistant = emptyAssistant(action.assistantId, action.mode, userId);
      assistant.status = "Connecting...";
      return {
        ...state,
        items: [...items, assistant],
        busy: true,
        activeChatId: action.assistantId,
        voiceUserId: action.mode === "voice" ? null : state.voiceUserId,
        lastVoiceAssistantId: action.mode === "voice" ? action.assistantId : state.lastVoiceAssistantId,
      };
    }
    case "chat.stopped":
    case "chat.interrupted":
    case "chat.failed": {
      const interrupted = action.type === "chat.interrupted";
      const next = updateAssistant(state, action.id, (a) => ({
        ...a,
        streaming: false,
        status: null,
        stopped: action.type === "chat.stopped",
        interrupted: a.interrupted || interrupted,
        error: action.type === "chat.failed" ? action.error : a.error,
        steps: a.steps.map((s) =>
          s.status === "running" ? { ...s, status: "error", summary: interrupted ? "Interrupted" : "Stopped" } : s,
        ),
      }));
      return state.activeChatId === action.id ? { ...next, busy: false, activeChatId: null } : next;
    }
    case "chat.closed": {
      // Stream ended (normally after turn.end; defensively if a proxy cut it mid-turn).
      const next = updateAssistant(state, action.id, (a) => (a.streaming ? { ...a, streaming: false, status: null } : a));
      return state.activeChatId === action.id ? { ...next, busy: false, activeChatId: null } : next;
    }
    case "assistant.interrupted":
      return updateAssistant(state, action.id, (a) => ({ ...a, interrupted: true }));
    case "remove":
      return { ...state, items: state.items.filter((it) => !action.ids.includes(it.id)) };
    case "voice.closed": {
      // Finalise anything left mid-stream when the socket closes.
      const live = new Set([state.voiceUserId, state.voiceAssistantId].filter(Boolean));
      return {
        ...state,
        items: state.items.map((it) =>
          live.has(it.id)
            ? it.kind === "user"
              ? { ...it, live: false }
              : { ...it, streaming: false, status: null }
            : it,
        ),
        voiceUserId: null,
        voiceAssistantId: null,
        voiceTurnId: null,
      };
    }
    case "ingest": {
      const { event, mode } = action;
      // Session-level events, identical for both modes.
      if (event.type === "customer") return { ...state, customer: event.customer };
      if (event.type === "trace") return { ...state, traces: upsertTrace(state.traces, event.trace) };
      let next = state;
      if (event.type === "escalation") next = { ...state, tickets: upsertTicket(state.tickets, event.ticket) };
      return mode === "text" ? ingestChat(next, event) : ingestVoice(next, event);
    }
  }
}
