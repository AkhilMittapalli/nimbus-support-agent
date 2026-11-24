"""HTTP + WebSocket routes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.context import ChaosConfig
from app.agent.events import AgentEvent, ErrorEvent
from app.api.rate_limit import ConcurrencyGuard, SlidingWindowLimiter, client_ip
from app.container import Container
from app.db.seed import DEMO_CUSTOMERS, reset_demo_data
from app.voice.bridge import VoiceBridge

log = structlog.get_logger(__name__)
router = APIRouter()

_limiter = SlidingWindowLimiter()
_voice_guard = ConcurrencyGuard()
_SSE_HEARTBEAT_S = 15.0


def container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


ContainerDep = Annotated[Container, Depends(container)]


# ---------------------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------------------
@router.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["ops"])
async def readyz(c: ContainerDep) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        await c.pool.fetchval("SELECT 1")
        checks["database"] = "ok"
        checks["kb_chunks"] = await c.kb.chunk_count()
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    checks["gemini_key"] = "configured" if c.settings.gemini_api_key.get_secret_value() else "missing"
    ok = checks["database"] == "ok" and checks["gemini_key"] == "configured" and checks.get("kb_chunks", 0) > 0
    if not ok:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=checks)
    return {"status": "ready", **checks}


# ---------------------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------------------
@router.post("/api/sessions", tags=["sessions"])
async def create_session(c: ContainerDep) -> dict[str, str]:
    session = await c.sessions.get_or_create(uuid.uuid4())
    return {"session_id": str(session.id)}


@router.get("/api/sessions/{session_id}", tags=["sessions"])
async def get_session(session_id: uuid.UUID, c: ContainerDep) -> dict[str, Any]:
    session = await c.sessions.get(session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    customer = await c.crm.customer(session.verified_customer_id) if session.verified_customer_id else None
    messages = await c.sessions.history(session_id, 200)
    tickets = await c.crm.open_tickets(session_id)
    return {
        "session_id": str(session.id),
        "status": session.status,
        "customer": customer.public_view() if customer else None,
        "tickets": [t.model_dump(mode="json") for t in tickets],
        "messages": [m.model_dump(mode="json") for m in messages],
    }


@router.get("/api/sessions/{session_id}/traces", tags=["observability"])
async def get_traces(session_id: uuid.UUID, c: ContainerDep) -> dict[str, Any]:
    return {"traces": await c.sessions.traces(session_id)}


# ---------------------------------------------------------------------------------------
# Chat (SSE)
# ---------------------------------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: uuid.UUID
    message: str = Field(min_length=1, max_length=8000)
    chaos: ChaosConfig | None = None
    # "voice" = turn from the cascaded voice pipeline (browser STT -> agent -> TTS fallback).
    mode: Literal["text", "voice"] = "text"


def _sse(event: AgentEvent) -> bytes:
    payload = event.model_dump(mode="json")
    return f"event: {payload['type']}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


@router.post("/api/chat", tags=["chat"])
async def chat(body: ChatRequest, request: Request, c: ContainerDep) -> StreamingResponse:
    ip = client_ip(request)
    allowed, retry_after = _limiter.check(f"chat:{ip}", limit=c.settings.rate_limit_chat_per_min)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limited",
                "message": "Too many messages - please wait a moment.",
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    async def emit(event: AgentEvent) -> None:
        await queue.put(event)

    async def run() -> None:
        try:
            await c.conversation.run_chat_turn(
                session_id=body.session_id, user_text=body.message, emit=emit, chaos=body.chaos, mode=body.mode
            )
        finally:
            await queue.put(None)

    async def stream() -> AsyncIterator[bytes]:
        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_SSE_HEARTBEAT_S)
                except TimeoutError:
                    yield b": keep-alive\n\n"
                    continue
                if event is None:
                    break
                yield _sse(event)
        except asyncio.CancelledError:
            log.info("chat.client_disconnected", session_id=str(body.session_id))
            raise
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ---------------------------------------------------------------------------------------
# Voice (WebSocket)
# ---------------------------------------------------------------------------------------
@router.websocket("/ws/voice")
async def voice(ws: WebSocket, session_id: uuid.UUID = Query(...), chaos_tool: str | None = Query(None)) -> None:
    c: Container = ws.app.state.container
    s = c.settings
    origin = ws.headers.get("origin")
    if origin and s.cors_origins != ["*"] and origin not in s.cors_origins:
        await ws.close(code=4403, reason="origin not allowed")
        return
    ip = client_ip(ws)
    allowed, _ = _limiter.check(f"voice:{ip}", limit=s.rate_limit_voice_per_min)
    if not allowed or not _voice_guard.acquire(ip, s.max_concurrent_voice_per_ip):
        await ws.accept()
        await ws.send_json(
            ErrorEvent(
                code="rate_limited", message="Too many voice sessions - please wait a moment.", retryable=True
            ).model_dump()
        )
        await ws.close(code=4429)
        return
    try:
        await ws.accept()
        chaos = ChaosConfig(tool_failure=chaos_tool) if chaos_tool else ChaosConfig()
        await VoiceBridge(c.conversation).run(ws, session_id, chaos)
    finally:
        _voice_guard.release(ip)
        with contextlib.suppress(Exception):
            await ws.close()


# ---------------------------------------------------------------------------------------
# Demo helpers (for reviewers)
# ---------------------------------------------------------------------------------------
@router.get("/api/demo/customers", tags=["demo"])
async def demo_customers(c: ContainerDep) -> dict[str, Any]:
    if not c.settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {
        "customers": [
            {
                "name": d.name,
                "email": d.email,
                "pin": d.pin,
                "tier": d.tier,
                "contract_status": d.contract_status,
                "scenario": d.scenario,
            }
            for d in DEMO_CUSTOMERS
        ]
    }


@router.post("/api/demo/reset", tags=["demo"])
async def demo_reset(request: Request, c: ContainerDep) -> dict[str, str]:
    if not c.settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    allowed, _ = _limiter.check(f"reset:{client_ip(request)}", limit=3)
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS)
    await reset_demo_data(c.pool)
    return {"status": "reset"}
