"""The agent orchestrator: a bounded ReAct-style loop over Gemini function calling.

State machine per turn:

    RECEIVED -> INPUT_GUARD -> LOAD_MEMORY -> [MODEL_STEP -> (TOOLS -> MODEL_STEP)*] -> GUARDRAIL
             -> (ANSWER | RETRACT+ESCALATE) -> PERSIST -> DONE
    any state -> ERROR (typed, user-facing, retryable flag)

Memory is the Postgres `messages` table shared by chat and voice, plus the authoritative session state
(verified customer, open tickets) injected into the system prompt every turn.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

import structlog
from google.genai import types

from app.agent.context import ChaosConfig, Deps, EventSink, TurnContext
from app.agent.escalation import auto_escalate
from app.agent.events import (
    CustomerUpdate,
    ErrorEvent,
    Guardrail,
    MessageDone,
    MessageReplace,
    Status,
    Token,
    TraceEvent,
    TurnEnd,
    TurnStart,
)
from app.agent.prompts import build_system_prompt
from app.db.repositories import Customer, Message, Session, Ticket
from app.errors import AppError, InputRejectedError
from app.guardrails.groundedness import OFF_TOPIC, GuardrailOutcome, check_groundedness
from app.guardrails.input_guard import check_input
from app.observability.tracer import TurnTrace
from app.tools.base import ToolRegistry

log = structlog.get_logger(__name__)

RETRACTION_TEXT = (
    "I want to be sure I give you accurate information, and I couldn't verify that answer against our "
    "official policies, so I've withdrawn it. I've opened ticket **{ticket}** so a member of our support team "
    "can confirm the details with you."
)
OUT_OF_SCOPE_TEXT = (
    "I'm Nova, Nimbus Cloud's support assistant, so I can only help with Nimbus Cloud accounts, billing, plans "
    "and troubleshooting. Is there anything about your Nimbus Cloud service I can help with?"
)
STEP_LIMIT_TEXT = (
    "I'm sorry - I wasn't able to complete this request automatically. I've opened ticket **{ticket}** so a "
    "member of our support team can pick it up from here."
)


def history_to_contents(history: list[Message]) -> list[types.Content]:
    """Convert stored messages (text + voice transcripts) into alternating Gemini contents."""
    contents: list[types.Content] = []
    for m in history:
        role = "user" if m.role == "user" else "model"
        text = m.content
        if contents and contents[-1].role == role:
            contents[-1].parts = [*(contents[-1].parts or []), types.Part(text=text)]
        else:
            contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    return contents


def seed_evidence_from_memory(
    ctx: TurnContext, history: list[Message], open_tickets: list[Ticket], limit: int = 4
) -> None:
    """Facts established earlier in the session are valid evidence for this turn's groundedness check:
    the authoritative account state, and prior assistant answers that themselves passed the guardrail."""
    if ctx.customer:
        ctx.add_evidence("session_state.verified_customer", ctx.customer.public_view())
    for t in open_tickets:
        ctx.add_evidence(f"session_state.open_ticket[{t.id}]", t.model_dump(mode="json"))
    verified = [
        m
        for m in history
        if m.role == "assistant" and (m.meta.get("guardrail") or {}).get("verdict") in ("grounded", "skipped")
    ]
    for m in verified[-limit:]:
        ctx.add_evidence(f"verified_earlier_answer[{m.turn_id}]", m.content)


class ConversationService:
    def __init__(self, deps: Deps, registry: ToolRegistry) -> None:
        self.deps = deps
        self.registry = registry

    # ------------------------------------------------------------------------------------
    # Shared state helpers (also used by the voice bridge)
    # ------------------------------------------------------------------------------------
    async def load_session(self, session_id: uuid.UUID) -> tuple[Session, Customer | None]:
        session = await self.deps.sessions.get_or_create(session_id)
        customer = None
        if session.verified_customer_id:
            customer = await self.deps.crm.customer(session.verified_customer_id)
        return session, customer

    async def system_prompt(self, ctx: TurnContext, mode: str) -> str:
        tickets = await self.deps.crm.open_tickets(ctx.session_id)
        session = await self.deps.sessions.get(ctx.session_id) or ctx.session
        return build_system_prompt(
            mode=mode, customer=ctx.customer, open_tickets=tickets, session_status=session.status
        )

    async def escalate_automatically(self, ctx: TurnContext, *, reason: str, summary: str) -> str:
        """Deterministic escalation used by guardrails when the model can't be trusted to do it."""
        return await auto_escalate(ctx, reason=reason, summary=summary)

    async def finalize_trace(self, ctx: TurnContext, outcome: dict[str, Any]) -> None:
        ctx.trace.outcome.update(outcome)
        ctx.trace.finish()
        data = ctx.trace.to_dict()
        await ctx.emit(TraceEvent(trace=data))
        try:
            await self.deps.sessions.add_trace(ctx.session_id, ctx.turn_id, ctx.mode, data)
        except Exception:
            log.exception("trace.persist_failed")

    # ------------------------------------------------------------------------------------
    # Chat turn
    # ------------------------------------------------------------------------------------
    async def run_chat_turn(
        self,
        *,
        session_id: uuid.UUID,
        user_text: str,
        emit: EventSink,
        chaos: ChaosConfig | None = None,
        mode: Literal["text", "voice"] = "text",
    ) -> None:
        s = self.deps.settings
        turn_id = uuid.uuid4().hex[:12]
        trace = TurnTrace(session_id=session_id, turn_id=turn_id, mode=mode)
        await emit(TurnStart(turn_id=turn_id, mode=mode))
        ctx: TurnContext | None = None
        try:
            checked = check_input(user_text, max_chars=s.max_message_chars)
            session, customer = await self.load_session(session_id)
            if session.turn_count >= s.max_turns_per_session:
                raise InputRejectedError(
                    "turn limit",
                    user_message="This conversation has reached its length limit. Please start a new chat.",
                )
            ctx = TurnContext(
                deps=self.deps,
                session=session,
                turn_id=turn_id,
                mode=mode,
                trace=trace,
                emit=emit,
                customer=customer,
                chaos=(chaos or ChaosConfig()) if s.enable_chaos else ChaosConfig(),
            )
            if checked.flags:
                trace.outcome["input_flags"] = checked.flags
                log.warning("input.flagged", flags=checked.flags, session_id=str(session_id))
            if customer:
                await emit(CustomerUpdate(customer=customer.public_view()))
            await self._agent_loop(ctx, checked.text)
        except AppError as exc:
            log.warning("turn.failed", code=exc.code, detail=exc.detail, session_id=str(session_id))
            await emit(ErrorEvent(code=exc.code, message=exc.user_message, retryable=exc.retryable))
            if ctx is not None:
                await self.finalize_trace(ctx, {"status": "error", "error_code": exc.code})
        except Exception:
            log.exception("turn.crashed", session_id=str(session_id))
            await emit(ErrorEvent(code="internal_error", message=AppError.user_message, retryable=True))
            if ctx is not None:
                await self.finalize_trace(ctx, {"status": "error", "error_code": "internal_error"})
        finally:
            await emit(TurnEnd(turn_id=turn_id))

    async def _agent_loop(self, ctx: TurnContext, user_text: str) -> None:
        s = self.deps.settings
        history = await self.deps.sessions.history(ctx.session_id, s.history_max_messages)
        contents = history_to_contents(history)
        contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        seed_evidence_from_memory(ctx, history, await self.deps.crm.open_tickets(ctx.session_id))
        system = await self.system_prompt(ctx, ctx.mode)
        tools = self.registry.gemini_tools()

        streamed: list[str] = []

        async def on_text(t: str) -> None:
            streamed.append(t)
            await ctx.emit(Token(text=t))

        async def on_retry(reason: str, next_model: str) -> None:
            label = {"llm_rate_limited": "Model is rate-limited", "llm_timeout": "Model timed out"}.get(
                reason, "Model error"
            )
            await ctx.emit(Status(label=f"{label} - retrying with {next_model}...", level="warning"))

        await ctx.emit(Status(label="Thinking..."))
        hit_step_limit = True
        for step_index in range(s.agent_max_steps):
            step = await self.deps.llm.stream_step(
                contents=contents,
                system_instruction=system,
                tools=tools,
                trace=ctx.trace,
                on_text=on_text,
                on_retry=on_retry,
                fault=ctx.chaos.llm_fault,
                step_index=step_index,
            )
            contents.append(step.content)
            if not step.function_calls:
                hit_step_limit = False
                break
            if streamed and not streamed[-1].endswith(("\n", " ")):
                await on_text("\n\n")
            responses: list[types.Part] = []
            for fc in step.function_calls:
                result = await self.registry.execute(ctx, fc.name or "", dict(fc.args or {}))
                responses.append(
                    types.Part(function_response=types.FunctionResponse(id=fc.id, name=fc.name, response=result))
                )
            contents.append(types.Content(role="user", parts=responses))
            # The session state may have changed (verification, escalation): refresh the system prompt.
            system = await self.system_prompt(ctx, ctx.mode)
            await ctx.emit(Status(label="Reviewing results..."))

        answer = "".join(streamed).strip()
        outcome: dict[str, Any] = {"status": "ok", "steps": step_index + 1, "tools": ctx.tools_called}

        if hit_step_limit or not answer:
            ticket = await self.escalate_automatically(
                ctx,
                reason="agent step limit reached" if hit_step_limit else "empty model answer",
                summary=f"Customer message: {user_text}",
            )
            answer = STEP_LIMIT_TEXT.format(ticket=ticket)
            await ctx.emit(MessageReplace(text=answer, reason="step_limit" if hit_step_limit else "empty_answer"))
            guard = GuardrailOutcome("none", None, "skipped", None, 0.0, "escalated", ["agent could not finish"])
        else:
            await ctx.emit(Status(label="Verifying answer against policy..."))
            guard = await self._apply_guardrail(ctx, answer)
            if guard.action == "retracted" and OFF_TOPIC in guard.reasons:
                answer = OUT_OF_SCOPE_TEXT
                await ctx.emit(MessageReplace(text=answer, reason="off_topic"))
            elif guard.action == "retracted":
                ticket = await self.escalate_automatically(
                    ctx,
                    reason="low-confidence answer retracted by guardrail",
                    summary=f"Customer asked: {user_text}\nRetracted draft: {answer[:600]}",
                )
                answer = RETRACTION_TEXT.format(ticket=ticket)
                await ctx.emit(MessageReplace(text=answer, reason="ungrounded"))

        # Low-confidence routing: a KB search found nothing reliable and no search found a high-confidence
        # answer -> deterministic hand-off to a human (the model is told this happens automatically).
        bands = {r.band for r in ctx.retrievals}
        if "low" in bands and "high" not in bands and guard.action == "answer":
            ticket = await auto_escalate(
                ctx,
                reason="no reliable knowledge-base answer (low retrieval confidence)",
                summary=f"Customer asked: {user_text}\n"
                f"Best KB similarity: {max(r.top_similarity for r in ctx.retrievals):.3f}",
                priority="low",
            )
            if ticket != "pending":
                note = f"\n\nI've routed this to a human specialist - your ticket ID is **{ticket}**."
                await ctx.emit(Token(text=note))
                answer += note
            guard.reasons.append("low retrieval confidence -> routed to human")
        if ctx.tickets and guard.action == "answer":
            guard.action = "escalated"
        await ctx.emit(Guardrail(**vars(guard)))
        await ctx.emit(MessageDone(text=answer))
        meta = {
            "guardrail": vars(guard),
            "tools": ctx.tools_called,
            "steps": ctx.steps,
            "tickets": [t.id for t in ctx.tickets],
        }
        await self.deps.sessions.add_message(ctx.session_id, ctx.turn_id, "user", ctx.mode, user_text)
        await self.deps.sessions.add_message(ctx.session_id, ctx.turn_id, "assistant", ctx.mode, answer, meta)
        await self.deps.sessions.increment_turns(ctx.session_id)
        outcome.update(guardrail=vars(guard), tickets=meta["tickets"], tool_failures=ctx.tool_failures)
        await self.finalize_trace(ctx, outcome)

    async def _apply_guardrail(self, ctx: TurnContext, answer: str) -> GuardrailOutcome:
        best = max(ctx.retrievals, key=lambda r: r.top_similarity, default=None)
        return await check_groundedness(
            llm=self.deps.llm,
            settings=self.deps.settings,
            trace=ctx.trace,
            answer=answer,
            evidence=ctx.evidence,
            retrieval_band=best.band if best else "none",
            top_similarity=round(best.top_similarity, 4) if best else None,
        )
