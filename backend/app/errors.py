"""Typed application errors. Each carries a stable `code` the UI maps to a fallback state."""

from __future__ import annotations


class AppError(Exception):
    code: str = "internal_error"
    retryable: bool = False
    user_message: str = "Something went wrong on our side. Please try again."

    def __init__(self, detail: str | None = None, *, user_message: str | None = None) -> None:
        super().__init__(detail or self.code)
        self.detail = detail or self.code
        if user_message:
            self.user_message = user_message


class LLMTimeoutError(AppError):
    code = "llm_timeout"
    retryable = True
    user_message = "The AI model took too long to respond. Please retry in a moment."


class LLMRateLimitError(AppError):
    code = "llm_rate_limited"
    retryable = True
    user_message = "The AI model is receiving too many requests right now. Please retry in a few seconds."


class LLMUnavailableError(AppError):
    code = "llm_unavailable"
    retryable = True
    user_message = "The AI model is temporarily unavailable. Your conversation is saved - please retry shortly."


class LLMBadRequestError(AppError):
    code = "llm_bad_request"
    retryable = False


class ToolExecutionError(AppError):
    code = "tool_failed"
    retryable = True


class RateLimitedError(AppError):
    code = "rate_limited"
    retryable = True
    user_message = "You're sending messages too quickly. Please wait a moment."


class InputRejectedError(AppError):
    code = "input_rejected"
    retryable = False
