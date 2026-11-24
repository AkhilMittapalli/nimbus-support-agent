"""Integration tests: real orchestrator + tools + Postgres, scripted fake LLM and fake KB.

Requires a Postgres with pgvector (docker compose up db). Uses a dedicated `support_test` database
and is skipped automatically when no database is reachable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import asyncpg
import pytest
from google.genai import types
from pydantic import BaseModel

from app.agent.context import ChaosConfig, Deps
from app.agent.events import AgentEvent
from app.agent.orchestrator import ConversationService
from app.config import Settings
from app.db.pool import create_pool, migrate
from app.db.repositories import CrmRepo, SessionRepo
from app.db.seed import reset_demo_data
from app.errors import LLMUnavailableError
from app.guardrails.groundedness import GroundednessJudgement
from app.llm.gemini import GeminiClient, ModelStep
from app.observability.tracer import TurnTrace
from app.rag.store import KnowledgeStore, RetrievalResult, RetrievedChunk
from app.tools.support_tools import build_registry

ADMIN_DSN = "postgresql://postgres:postgres@localhost:54329/postgres"
TEST_DSN = "postgresql://postgres:postgres@localhost:54329/support_test"


# ---------------------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------------------
def call(name: str, **args: Any) -> types.FunctionCall:
    return types.FunctionCall(id=uuid.uuid4().hex[:6], name=name, args=args)


class FakeLLM:
    """Replays a script of model steps: each item is either a list of function calls or final text."""

    def __init__(self, script: list[list[types.FunctionCall] | str], *, grounded: bool = True,
                 fail: bool = False) -> None:
        self.script = list(script)
        self.grounded = grounded
        self.fail = fail
        self.seen_contents: list[list[types.Content]] = []

    async def stream_step(self, *, contents: list[types.Content], on_text: Any, trace: TurnTrace,
                          **_: Any) -> ModelStep:
        if self.fail:
            raise LLMUnavailableError("scripted outage")
        self.seen_contents.append(list(contents))
        item = self.script.pop(0)
        trace.record_usage("fake-model", input_tokens=100, output_tokens=10)
        if isinstance(item, str):
            for word in item.split(" "):
                await on_text(word + " ")
            return ModelStep(model="fake", content=types.Content(role="model", parts=[types.Part(text=item)]),
                             text=item)
        parts = [types.Part(function_call=fc) for fc in item]
        return ModelStep(model="fake", content=types.Content(role="model", parts=parts), text="", function_calls=item)

    async def generate_json(self, *, schema: type[BaseModel], **_: Any) -> BaseModel:
        assert schema is GroundednessJudgement
        if self.grounded:
            return GroundednessJudgement(kind="factual", unsupported_claims=[], score=1.0)
        return GroundednessJudgement(kind="factual", unsupported_claims=["invented limit"], score=0.2)


class FakeKB:
    def __init__(self, similarity: float) -> None:
        self.similarity = similarity

    async def search(self, query: str, *, tier: str | None, trace: Any = None, top_k: int | None = None) -> RetrievalResult:
        chunk = RetrievedChunk(chunk_id=1, doc_id="refund-policy", title="Refund Policy",
                               section_path="Refund Policy > Refund limits by tier",
                               content="Pro: $200.00 per refund within 30 days.", category="billing",
                               tiers=["pro"], similarity=self.similarity, rrf_score=0.03)
        band = "high" if self.similarity >= 0.75 else "medium" if self.similarity >= 0.66 else "low"
        return RetrievalResult(query=query, tier_filter=tier, chunks=[chunk], top_similarity=self.similarity,
                               band=band)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
async def pool() -> AsyncIterator[asyncpg.Pool]:
    try:
        admin = await asyncpg.connect(ADMIN_DSN, timeout=3)
    except Exception:
        pytest.skip("Postgres not reachable on localhost:54329 (run: docker compose up -d db)")
    if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = 'support_test'"):
        await admin.execute("CREATE DATABASE support_test")
    await admin.close()
    settings = Settings(database_url=TEST_DSN)
    p = await create_pool(settings)
    await migrate(p, settings)
    yield p
    await p.close()


@pytest.fixture
async def service_factory(pool: asyncpg.Pool) -> AsyncIterator[Any]:
    await reset_demo_data(pool)

    def make(llm: FakeLLM, similarity: float = 0.8, **overrides: Any) -> ConversationService:
        settings = Settings(database_url=TEST_DSN, enable_chaos=True, **overrides)
        deps = Deps(settings=settings, llm=cast(GeminiClient, llm), kb=cast(KnowledgeStore, FakeKB(similarity)),
                    sessions=SessionRepo(pool), crm=CrmRepo(pool))
        return ConversationService(deps, build_registry())

    yield make


async def run_turn(svc: ConversationService, text: str, session_id: uuid.UUID | None = None,
                   chaos: ChaosConfig | None = None) -> tuple[uuid.UUID, list[dict[str, Any]]]:
    sid = session_id or uuid.uuid4()
    events: list[dict[str, Any]] = []

    async def emit(e: AgentEvent) -> None:
        events.append(e.model_dump(mode="json"))

    await svc.run_chat_turn(session_id=sid, user_text=text, emit=emit, chaos=chaos)
    return sid, events


def of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == kind]


# ---------------------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------------------
async def test_multi_step_refund_chain(service_factory: Any, pool: asyncpg.Pool) -> None:
    llm = FakeLLM([
        [call("verify_customer", email="bob@brightlabs.dev", pin="2222")],
        [call("get_account_overview")],
        [call("search_knowledge_base", query="refund limits pro")],
        [call("issue_refund", invoice_id="INV-9003", amount=149, reason="double charged")],
        "Done - refunded **$149.00** on INV-9003.",
    ])
    sid, events = await run_turn(service_factory(llm), "I'm bob, pin 2222, refund INV-9003")

    starts = [e["tool"] for e in of(events, "step.start")]
    assert starts == ["verify_customer", "get_account_overview", "search_knowledge_base", "issue_refund"]
    assert [e["status"] for e in of(events, "step.end")] == ["ok", "ok", "ok", "ok"]
    assert of(events, "step.start")[0]["args"]["pin"] == "****"  # PIN never leaves the server unredacted
    assert of(events, "customer")[0]["customer"]["tier"] == "pro"
    assert of(events, "guardrail")[0]["action"] == "answer"
    assert of(events, "message.done")[0]["text"].startswith("Done")
    trace = of(events, "trace")[0]["trace"]
    assert {s["kind"] for s in trace["spans"]} >= {"tool", "retrieval", "guardrail"}
    assert events[-1]["type"] == "turn.end"

    assert await pool.fetchval("SELECT refunded_cents FROM invoices WHERE id = 'INV-9003'") == 149_00
    assert await pool.fetchval("SELECT verified_customer_id FROM sessions WHERE id = $1", sid) == "cus_1002"
    assert await pool.fetchval("SELECT count(*) FROM messages WHERE session_id = $1", sid) == 2
    assert await pool.fetchval("SELECT count(*) FROM traces WHERE session_id = $1", sid) == 1


async def test_actions_blocked_until_verified(service_factory: Any, pool: asyncpg.Pool) -> None:
    llm = FakeLLM([[call("issue_refund", invoice_id="INV-9003", amount=10, reason="x")], "Please verify first."])
    _, events = await run_turn(service_factory(llm), "refund INV-9003")
    end = of(events, "step.end")[0]
    assert end["status"] == "blocked" and "verified" in end["summary"].lower()
    assert await pool.fetchval("SELECT refunded_cents FROM invoices WHERE id = 'INV-9003'") == 0


async def test_policy_blocks_over_limit_refund(service_factory: Any, pool: asyncpg.Pool) -> None:
    llm = FakeLLM([
        [call("verify_customer", email="carol@example.com", pin="3333")],
        [call("issue_refund", invoice_id="INV-9005", amount=29, reason="x")],  # within limits
        [call("issue_refund", invoice_id="INV-9006", amount=79, reason="x")],  # over limit + outside window
        "One refund done; the other needs Billing.",
    ])
    _, events = await run_turn(service_factory(llm), "refunds please")
    statuses = [e["status"] for e in of(events, "step.end")]
    assert statuses == ["ok", "ok", "blocked"]
    assert await pool.fetchval("SELECT refunded_cents FROM invoices WHERE id = 'INV-9006'") == 0


async def test_session_memory_spans_turns(service_factory: Any) -> None:
    llm = FakeLLM([[call("verify_customer", email="alice@acme.io", pin="1234")], "Hi Alice.",
                   [call("get_account_overview")], "You are on Enterprise."])
    svc = service_factory(llm)
    sid, _ = await run_turn(svc, "alice@acme.io 1234")
    _, events = await run_turn(svc, "what's my tier?", session_id=sid)
    assert of(events, "step.end")[0]["status"] == "ok"  # no re-verification needed
    second_turn_history = llm.seen_contents[2]
    assert any("Hi Alice" in (p.text or "") for c in second_turn_history for p in (c.parts or []))


async def test_ungrounded_answer_is_retracted_and_escalated(service_factory: Any, pool: asyncpg.Pool) -> None:
    llm = FakeLLM([[call("search_knowledge_base", query="refund")], "Pro refunds are up to $900 within 1 year."],
                  grounded=False)
    sid, events = await run_turn(service_factory(llm), "what is the pro refund limit?")
    assert of(events, "guardrail")[0]["action"] == "retracted"
    assert of(events, "message.replace")[0]["reason"] == "ungrounded"
    ticket_id = of(events, "escalation")[0]["ticket"]["id"]
    assert ticket_id in of(events, "message.done")[0]["text"]
    assert await pool.fetchval("SELECT status FROM sessions WHERE id = $1", sid) == "escalated"


async def test_low_confidence_retrieval_routes_to_human(service_factory: Any) -> None:
    llm = FakeLLM([[call("search_knowledge_base", query="HIPAA BAA")], "I can't confirm that from our documentation."])
    _, events = await run_turn(service_factory(llm, similarity=0.5), "Do you sign HIPAA BAAs?")
    guard = of(events, "guardrail")[0]
    assert guard["retrieval_band"] == "low" and guard["action"] == "escalated"
    assert of(events, "escalation")


async def test_tool_outage_is_reported_to_model_not_user_crash(service_factory: Any) -> None:
    llm = FakeLLM([[call("search_knowledge_base", query="sync")], "Search is down - want a ticket?"])
    _, events = await run_turn(service_factory(llm), "sync broken",
                               chaos=ChaosConfig(tool_failure="search_knowledge_base", tool_failure_mode="always"))
    assert of(events, "step.retry")
    assert of(events, "step.end")[0]["status"] == "error"
    tool_response = llm.seen_contents[1][-1].parts[0].function_response  # type: ignore[index]
    assert tool_response is not None and tool_response.response["error"] == "service_unavailable"
    assert not of(events, "error")


async def test_llm_outage_yields_retryable_error_and_persists_nothing(service_factory: Any, pool: asyncpg.Pool) -> None:
    sid, events = await run_turn(service_factory(FakeLLM([], fail=True)), "hello")
    err = of(events, "error")[0]
    assert err["code"] == "llm_unavailable" and err["retryable"] is True
    assert events[-1]["type"] == "turn.end"
    assert await pool.fetchval("SELECT count(*) FROM messages WHERE session_id = $1", sid) == 0


async def test_verification_lockout(service_factory: Any) -> None:
    script: list[list[types.FunctionCall] | str] = []
    for _ in range(4):
        script += [[call("verify_customer", email="bob@brightlabs.dev", pin="0000")], "Wrong."]
    svc = service_factory(FakeLLM(script))
    sid = uuid.uuid4()
    summaries = []
    for _ in range(4):
        _, events = await run_turn(svc, "try", session_id=sid)
        summaries.append(of(events, "step.end")[0]["summary"])
    assert summaries[-1] == "Verification locked"
