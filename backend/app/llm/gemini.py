"""Gemini access layer: streaming tool-calling steps, structured JSON calls and embeddings.

Resilience policy (per agent step):
  1. First-token timeout and total timeout on every streamed call.
  2. Retryable failures (429, 5xx, timeouts) are retried with exponential backoff + jitter, *only if
     no text has been streamed to the user yet* - we never splice two different generations.
  3. After `llm_max_retries` the step falls back to `fallback_model`.
  4. Anything else surfaces as a typed AppError the UI renders as a recoverable fallback state.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

import structlog
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from app.config import Settings
from app.errors import (
    AppError,
    LLMBadRequestError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.observability.tracer import TurnTrace

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)
LLMFault = Literal["none", "rate_limit_once", "timeout_once", "outage"]

TextCallback = Callable[[str], Awaitable[None]]
RetryCallback = Callable[[str, str], Awaitable[None]]  # (reason, next_model)


@dataclass
class ModelStep:
    """The result of one model call inside the agent loop."""

    model: str
    content: types.Content
    text: str
    function_calls: list[types.FunctionCall] = field(default_factory=list)
    finish_reason: str | None = None


def _map_error(exc: BaseException) -> AppError:
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, asyncio.TimeoutError | TimeoutError):
        return LLMTimeoutError(str(exc) or "timeout")
    if isinstance(exc, genai_errors.APIError):
        code = exc.code or 0
        if code == 429:
            return LLMRateLimitError(str(exc))
        if code in (408, 504):
            return LLMTimeoutError(str(exc))
        if code >= 500:
            return LLMUnavailableError(str(exc))
        return LLMBadRequestError(str(exc))
    return LLMUnavailableError(f"{type(exc).__name__}: {exc}")


def _backoff(attempt: int, *, rate_limited: bool) -> float:
    base = 1.0 if rate_limited else 0.4
    return float(min(6.0, base * (2**attempt)) * (0.75 + random.random() * 0.5))


def _preview(contents: Sequence[types.Content], limit: int = 4) -> list[dict[str, str]]:
    """Compact prompt preview for the observability drawer."""
    out: list[dict[str, str]] = []
    for c in list(contents)[-limit:]:
        texts: list[str] = []
        for p in c.parts or []:
            if p.text and not p.thought:
                texts.append(p.text)
            elif p.function_call:
                texts.append(f"[call {p.function_call.name}({p.function_call.args})]")
            elif p.function_response:
                texts.append(f"[result {p.function_response.name}]")
        out.append({"role": c.role or "", "text": " ".join(texts)[:600]})
    return out


class GeminiClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        api_key = settings.gemini_api_key.get_secret_value() or None
        self._client = genai.Client(api_key=api_key)
        # The Live (bidiGenerateContent) API is only served on v1beta/v1alpha; the SDK's default "v1"
        # rejects the WebSocket handshake with a bare HTTP 404.
        self._live_client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(api_version=settings.live_api_version)
        )

    @property
    def raw(self) -> genai.Client:
        return self._client

    @property
    def live(self) -> genai.Client:
        """Client pinned to the API version that serves the Live API."""
        return self._live_client

    # ------------------------------------------------------------------------------------
    # Streaming agent step
    # ------------------------------------------------------------------------------------
    def _model_chain(self) -> list[str]:
        s = self.settings
        chain = [s.agent_model] * (s.llm_max_retries + 1)
        if s.fallback_model and s.fallback_model != s.agent_model:
            chain.append(s.fallback_model)
        return chain

    def _step_config(self, model: str, system_instruction: str, tools: list[types.Tool]) -> types.GenerateContentConfig:
        thinking = None
        level = self.settings.agent_thinking_level.strip().upper()
        if level and model == self.settings.agent_model:
            thinking = types.ThinkingConfig(thinking_level=types.ThinkingLevel(level))
        return types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools or None,
            temperature=self.settings.agent_temperature,
            thinking_config=thinking,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    async def stream_step(
        self,
        *,
        contents: list[types.Content],
        system_instruction: str,
        tools: list[types.Tool],
        trace: TurnTrace,
        on_text: TextCallback,
        on_retry: RetryCallback,
        fault: LLMFault = "none",
        step_index: int = 0,
    ) -> ModelStep:
        chain = self._model_chain()
        last_error: AppError | None = None
        for attempt, model in enumerate(chain):
            with trace.span(f"llm.step[{step_index}]", "llm", model=model, attempt=attempt) as span:
                span.attrs["prompt_preview"] = _preview(contents)
                try:
                    step = await self._stream_once(
                        model=model,
                        contents=contents,
                        config=self._step_config(model, system_instruction, tools),
                        trace=trace,
                        span_attrs=span.attrs,
                        on_text=on_text,
                        fault=fault if step_index == 0 else "none",
                        attempt=attempt,
                    )
                    return step
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    err = _map_error(exc)
                    streamed_any_text = bool(span.attrs.get("streamed_chars"))
                    last_error = err
                    is_last = attempt == len(chain) - 1
                    if streamed_any_text or not err.retryable or is_last:
                        span.status = "error"
                        span.attrs["error"] = f"{err.code}: {err.detail}"[:300]
                        log.warning("llm.step_failed", code=err.code, model=model, attempt=attempt)
                        raise err from exc
                    span.status = "retry"
                    span.attrs["error"] = f"{err.code}: {err.detail}"[:300]
                    next_model = chain[attempt + 1]
                    log.info("llm.retry", code=err.code, model=model, next_model=next_model, attempt=attempt)
                    await on_retry(err.code, next_model)
            await asyncio.sleep(_backoff(attempt, rate_limited=isinstance(last_error, LLMRateLimitError)))
        raise last_error or LLMUnavailableError("no model attempts were made")

    async def _stream_once(
        self,
        *,
        model: str,
        contents: list[types.Content],
        config: types.GenerateContentConfig,
        trace: TurnTrace,
        span_attrs: dict[str, Any],
        on_text: TextCallback,
        fault: LLMFault,
        attempt: int,
    ) -> ModelStep:
        s = self.settings
        # Fault injection for resilience demos (enabled only when ENABLE_CHAOS=true).
        if fault == "outage" or (fault == "rate_limit_once" and attempt == 0):
            await asyncio.sleep(0.3)
            raise (LLMUnavailableError("injected outage") if fault == "outage" else LLMRateLimitError("injected 429"))
        if fault == "timeout_once" and attempt == 0:
            await asyncio.sleep(min(2.0, s.llm_first_token_timeout_s))
            raise LLMTimeoutError("injected first-token timeout")

        loop = asyncio.get_running_loop()
        started = loop.time()
        stream = await asyncio.wait_for(
            self._client.aio.models.generate_content_stream(model=model, contents=contents, config=config),
            timeout=s.llm_first_token_timeout_s,
        )
        iterator = stream.__aiter__()
        parts: list[types.Part] = []
        text_chunks: list[str] = []
        usage: types.GenerateContentResponseUsageMetadata | None = None
        finish_reason: str | None = None
        first_chunk_at: float | None = None

        while True:
            elapsed = loop.time() - started
            budget = (s.llm_first_token_timeout_s if first_chunk_at is None else s.llm_total_timeout_s) - elapsed
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout=max(0.5, budget))
            except StopAsyncIteration:
                break
            if first_chunk_at is None:
                first_chunk_at = loop.time()
                span_attrs["ttft_ms"] = round((first_chunk_at - started) * 1000, 1)
            if chunk.usage_metadata:
                usage = chunk.usage_metadata
            if not chunk.candidates:
                continue
            cand = chunk.candidates[0]
            if cand.finish_reason:
                finish_reason = str(
                    cand.finish_reason.value if hasattr(cand.finish_reason, "value") else cand.finish_reason
                )
            for part in cand.content.parts if cand.content and cand.content.parts else []:
                parts.append(part)
                if part.text and not part.thought:
                    text_chunks.append(part.text)
                    span_attrs["streamed_chars"] = span_attrs.get("streamed_chars", 0) + len(part.text)
                    trace.mark_first_token()
                    await on_text(part.text)

        function_calls = [p.function_call for p in parts if p.function_call]
        text = "".join(text_chunks)
        in_tok = (usage.prompt_token_count or 0) if usage else 0
        out_tok = (usage.candidates_token_count or 0) if usage else 0
        think_tok = (usage.thoughts_token_count or 0) if usage else 0
        cost = trace.record_usage(model, input_tokens=in_tok, output_tokens=out_tok, thinking_tokens=think_tok)
        span_attrs.update(
            input_tokens=in_tok,
            output_tokens=out_tok,
            thinking_tokens=think_tok,
            cost_usd=cost,
            finish_reason=finish_reason,
            function_calls=[fc.name for fc in function_calls],
            output_preview=text[:600],
        )
        if not parts:
            raise LLMUnavailableError(f"empty response (finish_reason={finish_reason})")
        return ModelStep(
            model=model,
            content=types.Content(role="model", parts=parts),
            text=text,
            function_calls=function_calls,
            finish_reason=finish_reason,
        )

    # ------------------------------------------------------------------------------------
    # Structured output (guardrail verifier)
    # ------------------------------------------------------------------------------------
    async def generate_json(
        self,
        *,
        model: str,
        prompt: str,
        schema: type[T],
        trace: TurnTrace,
        span_name: str,
        timeout_s: float,
        system_instruction: str | None = None,
    ) -> T:
        with trace.span(span_name, "llm", model=model) as span:
            last: AppError | None = None
            for attempt in range(2):
                try:
                    resp = await asyncio.wait_for(
                        self._client.aio.models.generate_content(
                            model=model,
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                system_instruction=system_instruction,
                                temperature=0.0,
                                response_mime_type="application/json",
                                response_json_schema=schema.model_json_schema(),
                            ),
                        ),
                        timeout=timeout_s,
                    )
                    u = resp.usage_metadata
                    in_tok = (u.prompt_token_count or 0) if u else 0
                    out_tok = ((u.candidates_token_count or 0) + (u.thoughts_token_count or 0)) if u else 0
                    span.attrs.update(
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        cost_usd=trace.record_usage(model, input_tokens=in_tok, output_tokens=out_tok),
                    )
                    return schema.model_validate_json(resp.text or "{}")
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    last = _map_error(exc) if not isinstance(exc, ValueError) else LLMBadRequestError(str(exc))
                    if not last.retryable or attempt == 1:
                        break
                    await asyncio.sleep(_backoff(attempt, rate_limited=isinstance(last, LLMRateLimitError)))
            span.status = "error"
            raise last or LLMUnavailableError("verifier failed")

    # ------------------------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------------------------
    async def embed(
        self, texts: list[str], *, task_type: Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]
    ) -> tuple[list[list[float]], int]:
        """Returns (vectors, estimated_input_tokens)."""
        s = self.settings
        vectors: list[list[float]] = []
        for i in range(0, len(texts), 50):
            batch = texts[i : i + 50]
            for attempt in range(4):
                try:
                    resp = await asyncio.wait_for(
                        self._client.aio.models.embed_content(
                            model=s.embedding_model,
                            # One Content per text: gemini-embedding-2 is multimodal and would otherwise
                            # aggregate a list of strings into a single embedding.
                            contents=[types.Content(parts=[types.Part(text=t)]) for t in batch],
                            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=s.embedding_dim),
                        ),
                        timeout=30,
                    )
                    vectors.extend([list(e.values or []) for e in resp.embeddings or []])
                    break
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    err = _map_error(exc)
                    if not err.retryable or attempt == 3:
                        raise err from exc
                    await asyncio.sleep(_backoff(attempt, rate_limited=isinstance(err, LLMRateLimitError)))
        if len(vectors) != len(texts) or any(len(v) != s.embedding_dim for v in vectors):
            raise LLMUnavailableError("embedding response had unexpected shape")
        return vectors, sum(len(t) // 4 for t in texts)
