"""Tool abstraction, registry and the resilient executor shared by chat and voice."""

from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Generic, Literal, TypeVar

import structlog
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.agent.context import TurnContext
from app.agent.events import StepEnd, StepRetry, StepStart
from app.errors import ToolExecutionError

log = structlog.get_logger(__name__)

A = TypeVar("A", bound=BaseModel)

_REDACTED_ARGS = {"pin"}


@dataclass
class ToolResult:
    """`data` is returned to the model; `summary` is the human-readable step outcome for the UI."""

    ok: bool
    data: dict[str, Any]
    summary: str
    blocked: bool = False  # policy said no (not a system failure)
    extra: dict[str, Any] = field(default_factory=dict)


class Tool(ABC, Generic[A]):
    name: ClassVar[str]
    description: ClassVar[str]
    label: ClassVar[str]  # UI status while running, e.g. "Searching knowledge base..."
    args_model: ClassVar[type[BaseModel]]
    requires_verification: ClassVar[bool] = False
    side_effect: ClassVar[bool] = False  # side-effecting tools are never auto-retried after partial success

    @abstractmethod
    async def run(self, ctx: TurnContext, args: A) -> ToolResult: ...

    def declaration(self) -> types.FunctionDeclaration:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=schema,
        )


def redact(args: dict[str, Any]) -> dict[str, Any]:
    return {k: ("****" if k in _REDACTED_ARGS else v) for k, v in args.items()}


class ToolRegistry:
    def __init__(self, tools: list[Tool[Any]]) -> None:
        self._tools = {t.name: t for t in tools}

    def gemini_tools(self, *, live: bool = False) -> list[types.Tool]:
        decls = [t.declaration() for t in self._tools.values()]
        if live:
            # Chained tools depend on each other's results: run them synchronously in voice too.
            for d in decls:
                d.behavior = types.Behavior.BLOCKING
        return [types.Tool(function_declarations=decls)]

    def names(self) -> list[str]:
        return list(self._tools)

    async def execute(self, ctx: TurnContext, name: str, raw_args: dict[str, Any] | None) -> dict[str, Any]:
        """Run a tool call end-to-end: validation, verification gate, timeout, retry, chaos, events.

        Always returns a JSON-serialisable dict for the model - failures are reported to the
        model as data so it can recover (apologise, try another path, or escalate).
        """
        step_id = uuid.uuid4().hex[:8]
        tool = self._tools.get(name)
        args = dict(raw_args or {})
        ctx.tools_called.append(name)
        label = tool.label if tool else f"Running {name}..."
        await ctx.emit(StepStart(step_id=step_id, tool=name, label=label, args=redact(args)))
        started = time.perf_counter()

        retries: list[str] = []

        async def finish(
            status: Literal["ok", "error", "blocked"], summary: str, payload: dict[str, Any]
        ) -> dict[str, Any]:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            await ctx.emit(StepEnd(step_id=step_id, tool=name, status=status, duration_ms=duration_ms, summary=summary))
            # Compact record persisted with the assistant message so restored conversations show steps.
            ctx.steps.append(
                {
                    "id": step_id,
                    "tool": name,
                    "label": label,
                    "status": status,
                    "duration_ms": duration_ms,
                    "summary": summary,
                    "retries": list(retries),
                }
            )
            return payload

        with ctx.trace.span(f"tool.{name}", "tool", args=redact(args)) as span:
            if tool is None:
                span.status = "error"
                return await finish(
                    "error", "Unknown tool", {"error": "unknown_tool", "message": f"No tool named {name}."}
                )

            if tool.requires_verification and ctx.customer is None:
                span.status = "blocked"
                return await finish(
                    "blocked",
                    "Identity not verified",
                    {
                        "error": "verification_required",
                        "message": "The customer must be verified with verify_customer (email + 4-digit PIN) first.",
                    },
                )

            try:
                parsed = tool.args_model.model_validate(args)
            except ValidationError as exc:
                span.status = "error"
                errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
                return await finish("error", "Invalid arguments", {"error": "invalid_arguments", "details": errors})

            settings = ctx.deps.settings
            attempts = 1 + (settings.tool_max_retries if not tool.side_effect else 0)
            last_exc: Exception | None = None
            for attempt in range(attempts):
                try:
                    self._maybe_inject_failure(ctx, name)
                    result = await asyncio.wait_for(tool.run(ctx, parsed), timeout=settings.tool_timeout_s)
                    span.attrs.update(ok=result.ok, blocked=result.blocked, summary=result.summary, attempt=attempt)
                    if result.blocked:
                        span.status = "blocked"
                    status: Literal["ok", "error", "blocked"] = (
                        "ok" if result.ok else ("blocked" if result.blocked else "error")
                    )
                    return await finish(status, result.summary, result.data)
                except (ToolExecutionError, TimeoutError) as exc:
                    last_exc = exc
                    reason = "timeout" if not isinstance(exc, ToolExecutionError) else exc.detail
                    log.warning("tool.failed", tool=name, attempt=attempt, reason=reason)
                    if attempt + 1 < attempts:
                        span.attrs.setdefault("retries", []).append(reason)
                        retries.append(reason)
                        await ctx.emit(StepRetry(step_id=step_id, tool=name, attempt=attempt + 1, reason=reason))
                        await asyncio.sleep(0.4 * (2**attempt))
                except Exception as exc:  # unexpected bug in a tool: never crash the conversation
                    last_exc = exc
                    log.exception("tool.crashed", tool=name)
                    break

            span.status = "error"
            span.attrs["error"] = str(last_exc)[:300]
            ctx.tool_failures.append(name)
            return await finish(
                "error",
                "Service unavailable",
                {
                    "error": "service_unavailable",
                    "message": f"The {name} system is temporarily unavailable. Do not retry it this turn; tell the "
                    "customer, and offer to create an escalation ticket so a human can follow up.",
                },
            )

    @staticmethod
    def _maybe_inject_failure(ctx: TurnContext, name: str) -> None:
        chaos = ctx.chaos
        if not ctx.deps.settings.enable_chaos or not chaos.tool_failure:
            return
        if chaos.tool_failure not in ("any", name):
            return
        n = ctx._tool_attempts.get(name, 0)
        ctx._tool_attempts[name] = n + 1
        if chaos.tool_failure_mode == "always" or n == 0:
            raise ToolExecutionError(f"injected failure in {name} (attempt {n + 1})")
