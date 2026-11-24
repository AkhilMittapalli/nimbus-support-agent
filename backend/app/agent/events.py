"""Event protocol streamed to the UI (SSE for chat, WebSocket JSON frames for voice).

Every event is a JSON object with a `type` discriminator. The frontend mirrors these in
`frontend/lib/protocol.ts`; keep the two in sync.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class _Event(BaseModel):
    type: str


class TurnStart(_Event):
    type: Literal["turn.start"] = "turn.start"
    turn_id: str
    mode: Literal["text", "voice"]


class Status(_Event):
    """Free-form status line ("Thinking...", "Retrying with backup model...")."""

    type: Literal["status"] = "status"
    label: str
    level: Literal["info", "warning"] = "info"


class StepStart(_Event):
    type: Literal["step.start"] = "step.start"
    step_id: str
    tool: str
    label: str
    args: dict[str, Any]


class StepRetry(_Event):
    type: Literal["step.retry"] = "step.retry"
    step_id: str
    tool: str
    attempt: int
    reason: str


class StepEnd(_Event):
    type: Literal["step.end"] = "step.end"
    step_id: str
    tool: str
    status: Literal["ok", "error", "blocked"]
    duration_ms: float
    summary: str


class Token(_Event):
    type: Literal["token"] = "token"
    text: str


class MessageReplace(_Event):
    """Guardrail retracted the streamed answer; UI swaps the bubble content."""

    type: Literal["message.replace"] = "message.replace"
    text: str
    reason: str


class MessageDone(_Event):
    type: Literal["message.done"] = "message.done"
    text: str


class CustomerUpdate(_Event):
    type: Literal["customer"] = "customer"
    customer: dict[str, Any] | None


class Escalation(_Event):
    type: Literal["escalation"] = "escalation"
    ticket: dict[str, Any]


class Guardrail(_Event):
    type: Literal["guardrail"] = "guardrail"
    retrieval_band: Literal["high", "medium", "low", "none"]
    top_similarity: float | None
    verdict: Literal["grounded", "ungrounded", "skipped", "error"]
    groundedness: float | None
    confidence: float
    action: Literal["answer", "retracted", "escalated", "flagged"]
    reasons: list[str]


class TraceEvent(_Event):
    type: Literal["trace"] = "trace"
    trace: dict[str, Any]


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    code: str
    message: str
    retryable: bool


class TurnEnd(_Event):
    type: Literal["turn.end"] = "turn.end"
    turn_id: str


# ---- voice-only ---------------------------------------------------------------------------
class Transcript(_Event):
    type: Literal["transcript"] = "transcript"
    role: Literal["user", "assistant"]
    text: str
    final: bool


class Interrupted(_Event):
    type: Literal["interrupted"] = "interrupted"


class VoiceState(_Event):
    type: Literal["voice.state"] = "voice.state"
    state: Literal["connecting", "ready", "listening", "thinking", "speaking", "closed"]
    detail: str | None = None


AgentEvent = (
    TurnStart
    | Status
    | StepStart
    | StepRetry
    | StepEnd
    | Token
    | MessageReplace
    | MessageDone
    | CustomerUpdate
    | Escalation
    | Guardrail
    | TraceEvent
    | ErrorEvent
    | TurnEnd
    | Transcript
    | Interrupted
    | VoiceState
)
