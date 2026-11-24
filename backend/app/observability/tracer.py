"""Per-turn tracing: spans for LLM calls, tools, retrieval and guardrails + token/cost roll-up.

A `TurnTrace` is created per user turn (chat or voice), streamed to the UI's observability drawer
as a `trace` event, and persisted to the `traces` table.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import structlog

from app.observability.pricing import estimate_cost

log = structlog.get_logger(__name__)

SpanKind = Literal["llm", "tool", "retrieval", "guardrail", "voice", "system"]


def _now_ms() -> float:
    return time.perf_counter() * 1000


@dataclass
class Span:
    name: str
    kind: SpanKind
    start_ms: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    duration_ms: float | None = None
    status: Literal["ok", "error", "retry", "blocked"] = "ok"
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, origin_ms: float) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "start_ms": round(self.start_ms - origin_ms, 1),
            "duration_ms": round(self.duration_ms, 1) if self.duration_ms is not None else None,
            "status": self.status,
            "attrs": self.attrs,
        }


@dataclass
class ModelUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    audio_input_tokens: int = 0
    audio_output_tokens: int = 0
    cost_usd: float = 0.0


class TurnTrace:
    def __init__(self, *, session_id: uuid.UUID, turn_id: str, mode: Literal["text", "voice"]) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.mode = mode
        self.started_at = datetime.now(UTC)
        self.origin_ms = _now_ms()
        self.spans: list[Span] = []
        self.usage: dict[str, ModelUsage] = {}
        self.first_token_ms: float | None = None
        self.end_ms: float | None = None
        self.outcome: dict[str, Any] = {}

    @contextmanager
    def span(self, name: str, kind: SpanKind, **attrs: Any) -> Iterator[Span]:
        s = Span(name=name, kind=kind, start_ms=_now_ms(), attrs=dict(attrs))
        self.spans.append(s)
        try:
            yield s
        except BaseException as exc:
            s.status = "error"
            s.attrs.setdefault("error", f"{type(exc).__name__}: {exc}"[:300])
            raise
        finally:
            s.duration_ms = _now_ms() - s.start_ms

    def mark_first_token(self) -> None:
        if self.first_token_ms is None:
            self.first_token_ms = _now_ms() - self.origin_ms

    def record_usage(
        self,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        thinking_tokens: int = 0,
        audio_input_tokens: int = 0,
        audio_output_tokens: int = 0,
    ) -> float:
        u = self.usage.setdefault(model, ModelUsage())
        cost = estimate_cost(
            model,
            input_tokens=input_tokens,
            # Thinking tokens are billed as output tokens.
            output_tokens=output_tokens + thinking_tokens,
            audio_input_tokens=audio_input_tokens,
            audio_output_tokens=audio_output_tokens,
        )
        u.calls += 1
        u.input_tokens += input_tokens
        u.output_tokens += output_tokens
        u.thinking_tokens += thinking_tokens
        u.audio_input_tokens += audio_input_tokens
        u.audio_output_tokens += audio_output_tokens
        u.cost_usd = round(u.cost_usd + cost, 8)
        return cost

    def finish(self) -> None:
        if self.end_ms is None:
            self.end_ms = _now_ms() - self.origin_ms

    def to_dict(self) -> dict[str, Any]:
        totals = ModelUsage()
        for u in self.usage.values():
            totals.calls += u.calls
            totals.input_tokens += u.input_tokens
            totals.output_tokens += u.output_tokens
            totals.thinking_tokens += u.thinking_tokens
            totals.audio_input_tokens += u.audio_input_tokens
            totals.audio_output_tokens += u.audio_output_tokens
            totals.cost_usd = round(totals.cost_usd + u.cost_usd, 8)
        return {
            "turn_id": self.turn_id,
            "session_id": str(self.session_id),
            "mode": self.mode,
            "started_at": self.started_at.isoformat(),
            "latency": {
                "first_token_ms": round(self.first_token_ms, 1) if self.first_token_ms is not None else None,
                "total_ms": round(self.end_ms, 1) if self.end_ms is not None else None,
            },
            "usage": {model: vars(u) for model, u in self.usage.items()},
            "totals": vars(totals),
            "spans": [s.to_dict(self.origin_ms) for s in self.spans],
            "outcome": self.outcome,
        }
