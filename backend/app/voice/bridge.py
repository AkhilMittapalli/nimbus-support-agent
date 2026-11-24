"""Bidirectional voice bridge: browser <-WebSocket-> FastAPI <-Live API-> Gemini.

Why a server-side proxy instead of connecting the browser straight to Gemini with an ephemeral token?
  * Tools run server-side with the *same* registry, verification gate, policy engine and tracing as chat.
  * Voice transcripts land in the same `messages` table, so memory is shared across modes.
  * The API key never leaves the server, and rate limits/session caps are enforced in one place.
The cost is one extra network hop (~10-40 ms on a co-located deployment), documented in the README.

Browser -> server frames:  binary = PCM16 LE mono 16 kHz mic audio; JSON {"type": "text" | "end"}.
Server -> browser frames:  binary = PCM16 LE mono 24 kHz model audio; JSON = agent events (see events.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable
from typing import Any

import structlog
from fastapi import WebSocket, WebSocketDisconnect
from google.genai import types

from app.agent.context import ChaosConfig, TurnContext
from app.agent.events import (
    AgentEvent,
    CustomerUpdate,
    ErrorEvent,
    Guardrail,
    Interrupted,
    Status,
    Transcript,
    TurnEnd,
    TurnStart,
    VoiceState,
)
from app.agent.orchestrator import ConversationService, history_to_contents
from app.guardrails.groundedness import check_groundedness
from app.observability.tracer import TurnTrace

log = structlog.get_logger(__name__)

_MAX_AUDIO_FRAME = 64 * 1024


class _VoiceTurn:
    """Accumulates one user->assistant exchange inside a continuous voice session."""

    def __init__(self, ctx: TurnContext) -> None:
        self.ctx = ctx
        self.user_text = ""
        self.assistant_text = ""
        self.last_user_audio_at: float | None = None
        self.first_audio_out_at: float | None = None
        self.interrupted = False
        self.started = False

    @property
    def empty(self) -> bool:
        return not self.user_text.strip() and not self.assistant_text.strip() and not self.ctx.tools_called


class VoiceBridge:
    def __init__(self, conversation: ConversationService) -> None:
        self.conv = conversation
        self.deps = conversation.deps

    async def run(self, ws: WebSocket, session_id: uuid.UUID, chaos: ChaosConfig) -> None:
        send_lock = asyncio.Lock()
        closed = asyncio.Event()

        async def emit(event: AgentEvent) -> None:
            if closed.is_set():
                return
            async with send_lock:
                with contextlib.suppress(Exception):
                    await ws.send_json(event.model_dump(mode="json"))

        async def send_audio(data: bytes) -> None:
            if closed.is_set():
                return
            async with send_lock:
                with contextlib.suppress(Exception):
                    await ws.send_bytes(data)

        s = self.deps.settings
        await emit(VoiceState(state="connecting"))
        session, customer = await self.conv.load_session(session_id)
        if customer:
            await emit(CustomerUpdate(customer=customer.public_view()))

        def new_turn() -> _VoiceTurn:
            nonlocal customer
            turn_id = uuid.uuid4().hex[:12]
            ctx = TurnContext(
                deps=self.deps,
                session=session,
                turn_id=turn_id,
                mode="voice",
                trace=TurnTrace(session_id=session_id, turn_id=turn_id, mode="voice"),
                emit=emit,
                customer=customer,
                chaos=chaos if s.enable_chaos else ChaosConfig(),
            )
            return _VoiceTurn(ctx)

        turn = new_turn()
        history = await self.deps.sessions.history(session_id, s.history_max_messages)
        system_prompt = await self.conv.system_prompt(turn.ctx, "voice")
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=system_prompt,
            tools=self.conv.registry.gemini_tools(live=True),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=s.live_voice))
            ),
            context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
            history_config=types.HistoryConfig(initial_history_in_client_content=True) if history else None,
        )

        connected = False
        try:
            async with self.deps.llm.raw.aio.live.connect(model=s.live_model, config=config) as live:
                if history:
                    # Shared memory: replay prior chat/voice turns so the caller can switch modes seamlessly.
                    seed_turns: list[types.Content | types.ContentDict] = list(history_to_contents(history))
                    await live.send_client_content(turns=seed_turns, turn_complete=False)
                connected = True
                await emit(VoiceState(state="ready"))

                async def finalize(t: _VoiceTurn) -> None:
                    nonlocal customer
                    if t.empty:
                        return
                    customer = t.ctx.customer or customer
                    await self._finalize_turn(t)

                async def upstream() -> None:
                    while True:
                        msg = await ws.receive()
                        if msg.get("type") == "websocket.disconnect":
                            return
                        if (data := msg.get("bytes")) is not None:
                            if len(data) > _MAX_AUDIO_FRAME:
                                continue
                            await live.send_realtime_input(
                                audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                            )
                        elif (text := msg.get("text")) is not None:
                            payload = _safe_json(text)
                            if payload.get("type") == "end":
                                return
                            if payload.get("type") == "text" and isinstance(payload.get("text"), str):
                                await live.send_realtime_input(text=payload["text"][: s.max_message_chars])

                async def downstream() -> None:
                    nonlocal turn
                    pending_tools: dict[str, asyncio.Task[None]] = {}
                    while True:
                        async for msg in live.receive():
                            if msg.go_away is not None:
                                await emit(
                                    Status(
                                        label="Voice session is about to time out - reconnect to continue.",
                                        level="warning",
                                    )
                                )
                            if msg.usage_metadata is not None:
                                _record_live_usage(turn.ctx.trace, s.live_model, msg.usage_metadata)
                            if msg.tool_call_cancellation is not None:
                                for cid in msg.tool_call_cancellation.ids or []:
                                    if (task := pending_tools.pop(cid, None)) is not None:
                                        task.cancel()
                            if msg.tool_call is not None:
                                await emit(VoiceState(state="thinking"))
                                for fc in msg.tool_call.function_calls or []:
                                    key = fc.id or uuid.uuid4().hex
                                    task = asyncio.create_task(self._run_tool(live, turn, fc))
                                    pending_tools[key] = task
                                    task.add_done_callback(_discard(pending_tools, key))
                            sc = msg.server_content
                            if sc is None:
                                continue
                            if sc.input_transcription and sc.input_transcription.text:
                                if not turn.started:
                                    turn.started = True
                                    await emit(TurnStart(turn_id=turn.ctx.turn_id, mode="voice"))
                                    await emit(VoiceState(state="listening"))
                                turn.user_text += sc.input_transcription.text
                                turn.last_user_audio_at = time.perf_counter()
                                await emit(Transcript(role="user", text=turn.user_text.strip(), final=False))
                            if sc.model_turn and sc.model_turn.parts:
                                for part in sc.model_turn.parts:
                                    if part.inline_data and part.inline_data.data:
                                        if turn.first_audio_out_at is None:
                                            turn.first_audio_out_at = time.perf_counter()
                                            turn.ctx.trace.mark_first_token()
                                            await emit(VoiceState(state="speaking"))
                                        await send_audio(part.inline_data.data)
                            if sc.output_transcription and sc.output_transcription.text:
                                turn.assistant_text += sc.output_transcription.text
                                await emit(Transcript(role="assistant", text=turn.assistant_text.strip(), final=False))
                            if sc.interrupted:
                                # Barge-in: the client must flush queued playback immediately.
                                turn.interrupted = True
                                await emit(Interrupted())
                                done, turn = turn, new_turn()
                                await finalize(done)
                                await emit(VoiceState(state="listening"))
                            elif sc.turn_complete:
                                done, turn = turn, new_turn()
                                await finalize(done)
                                await emit(VoiceState(state="ready"))
                        # receive() returns after each complete turn; loop to keep the session alive.

                tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
                done_tasks, pending = await asyncio.wait(
                    tasks, timeout=s.voice_max_session_s, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                if not done_tasks:
                    await emit(Status(label="Voice session time limit reached.", level="warning"))
                for t in done_tasks:
                    if (exc := t.exception()) is not None and not isinstance(exc, WebSocketDisconnect):
                        raise exc
                await finalize(turn)
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("voice.session_failed", session_id=str(session_id), connected=connected)
            if connected:
                await emit(
                    ErrorEvent(
                        code="voice_unavailable",
                        message="The voice connection dropped. Your conversation is saved - reconnect, or continue in chat.",
                        retryable=True,
                    )
                )
            else:
                # Live API refused the session (quota, region, outage): the UI switches to the cascaded
                # STT -> agent -> TTS fallback, which runs through the regular chat pipeline.
                await emit(
                    ErrorEvent(
                        code="live_unavailable",
                        message="Realtime voice is unavailable right now - switching to the standard voice pipeline.",
                        retryable=True,
                    )
                )
        finally:
            await emit(VoiceState(state="closed"))
            closed.set()

    async def _run_tool(self, live: Any, turn: _VoiceTurn, fc: types.FunctionCall) -> None:
        result = await self.conv.registry.execute(turn.ctx, fc.name or "", dict(fc.args or {}))
        await live.send_tool_response(
            function_responses=[types.FunctionResponse(id=fc.id, name=fc.name, response=result)]
        )

    async def _finalize_turn(self, t: _VoiceTurn) -> None:
        ctx = t.ctx
        user_text = t.user_text.strip()
        assistant_text = t.assistant_text.strip() + (" [interrupted]" if t.interrupted else "")
        if t.last_user_audio_at and t.first_audio_out_at and t.first_audio_out_at > t.last_user_audio_at:
            ctx.trace.outcome["voice_response_latency_ms"] = round(
                (t.first_audio_out_at - t.last_user_audio_at) * 1000, 1
            )
        await ctx.emit(Transcript(role="user", text=user_text, final=True))
        await ctx.emit(Transcript(role="assistant", text=assistant_text, final=True))

        # Audio can't be retracted once spoken, so in voice the guardrail runs as a post-hoc audit:
        # the pre-generation gate (low-confidence retrieval returns no passages) is what prevents
        # hallucination; ungrounded answers are flagged in the UI and escalated for human review.
        best = max(ctx.retrievals, key=lambda r: r.top_similarity, default=None)
        guard = await check_groundedness(
            llm=self.deps.llm,
            settings=self.deps.settings,
            trace=ctx.trace,
            answer=t.assistant_text,
            evidence=ctx.evidence,
            retrieval_band=best.band if best else "none",
            top_similarity=round(best.top_similarity, 4) if best else None,
        )
        if guard.action == "retracted":
            guard.action = "flagged"
            await self.conv.escalate_automatically(
                ctx,
                reason="voice answer failed post-hoc groundedness audit",
                summary=f"Caller said: {user_text}\nAssistant said: {t.assistant_text[:600]}",
            )
        await ctx.emit(Guardrail(**vars(guard)))

        meta = {
            "guardrail": vars(guard),
            "tools": ctx.tools_called,
            "tickets": [x.id for x in ctx.tickets],
            "interrupted": t.interrupted,
        }
        if user_text:
            await self.deps.sessions.add_message(ctx.session_id, ctx.turn_id, "user", "voice", user_text)
        if t.assistant_text.strip():
            await self.deps.sessions.add_message(
                ctx.session_id, ctx.turn_id, "assistant", "voice", assistant_text, meta
            )
        await self.deps.sessions.increment_turns(ctx.session_id)
        await self.conv.finalize_trace(
            ctx,
            {
                "status": "ok",
                "tools": ctx.tools_called,
                "guardrail": vars(guard),
                "interrupted": t.interrupted,
                "tickets": meta["tickets"],
            },
        )
        await ctx.emit(TurnEnd(turn_id=ctx.turn_id))


def _record_live_usage(trace: TurnTrace, model: str, usage: types.UsageMetadata) -> None:
    def split(details: list[types.ModalityTokenCount] | None, total: int) -> tuple[int, int]:
        audio = sum(d.token_count or 0 for d in details or [] if d.modality == types.MediaModality.AUDIO)
        return audio, max(0, total - audio)

    audio_in, text_in = split(usage.prompt_tokens_details, usage.prompt_token_count or 0)
    audio_out, text_out = split(usage.response_tokens_details, usage.response_token_count or 0)
    trace.record_usage(
        model,
        input_tokens=text_in,
        output_tokens=text_out + (usage.thoughts_token_count or 0),
        audio_input_tokens=audio_in,
        audio_output_tokens=audio_out,
    )


def _discard(tasks: dict[str, asyncio.Task[None]], key: str) -> Callable[[asyncio.Task[None]], None]:
    def callback(_task: asyncio.Task[None]) -> None:
        tasks.pop(key, None)

    return callback


def _safe_json(text: str) -> dict[str, Any]:
    import json

    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
