"""Per-turn context shared by the orchestrator, tools and guardrails."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from app.agent.events import AgentEvent
from app.config import Settings
from app.db.repositories import CrmRepo, Customer, Session, SessionRepo, Ticket
from app.llm.gemini import GeminiClient, LLMFault
from app.observability.tracer import TurnTrace
from app.rag.store import KnowledgeStore, RetrievalResult

EventSink = Callable[[AgentEvent], Awaitable[None]]


class ChaosConfig(BaseModel):
    """Fault injection for demonstrating graceful degradation. Ignored unless ENABLE_CHAOS=true."""

    tool_failure: str | None = None  # tool name, or "any"
    tool_failure_mode: Literal["once", "always"] = "once"
    llm_fault: LLMFault = "none"


@dataclass
class Deps:
    settings: Settings
    llm: GeminiClient
    kb: KnowledgeStore
    sessions: SessionRepo
    crm: CrmRepo


@dataclass
class Evidence:
    """Facts gathered during the turn; the groundedness verifier checks answers against these."""

    source: str
    content: str


@dataclass
class TurnContext:
    deps: Deps
    session: Session
    turn_id: str
    mode: Literal["text", "voice"]
    trace: TurnTrace
    emit: EventSink
    customer: Customer | None = None
    chaos: ChaosConfig = field(default_factory=ChaosConfig)
    evidence: list[Evidence] = field(default_factory=list)
    retrievals: list[RetrievalResult] = field(default_factory=list)
    tickets: list[Ticket] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    tool_failures: list[str] = field(default_factory=list)
    _tool_attempts: dict[str, int] = field(default_factory=dict)

    @property
    def session_id(self) -> uuid.UUID:
        return self.session.id

    def add_evidence(self, source: str, content: Any) -> None:
        self.evidence.append(Evidence(source=source, content=str(content)))
