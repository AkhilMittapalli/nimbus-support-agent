"""Estimated Gemini API pricing (USD per 1M tokens) used for the observability cost estimate.

Figures reflect the public Gemini Developer API pricing page at the time of writing (Nov 2026,
including introductory rates). They are *estimates*; override them with the PRICING_OVERRIDES env
var (JSON: {"model": {"input": x, "output": y, "audio_input": z, "audio_output": w}}) without a
redeploy of code.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input: float
    output: float
    audio_input: float | None = None
    audio_output: float | None = None


_DEFAULT_PRICES: dict[str, ModelPrice] = {
    "gemini-3.8-flash": ModelPrice(input=0.75, output=3.75),
    "gemini-3.5-flash-lite": ModelPrice(input=0.30, output=2.50),
    "gemini-3.1-flash-lite": ModelPrice(input=0.25, output=1.50, audio_input=0.50),
    "gemini-embedding-2": ModelPrice(input=0.15, output=0.0),
    "gemini-embedding-001": ModelPrice(input=0.15, output=0.0),
    # Live (native audio) pricing is billed per modality; values are estimates.
    "gemini-3.8-live": ModelPrice(input=0.75, output=4.50, audio_input=3.00, audio_output=12.00),
}

_FALLBACK = ModelPrice(input=1.00, output=5.00, audio_input=3.00, audio_output=12.00)


def _load() -> dict[str, ModelPrice]:
    prices = dict(_DEFAULT_PRICES)
    raw = os.getenv("PRICING_OVERRIDES")
    if raw:
        for model, p in json.loads(raw).items():
            prices[model] = ModelPrice(**p)
    return prices


PRICES = _load()


def price_for(model: str) -> ModelPrice:
    return PRICES.get(model, _FALLBACK)


def estimate_cost(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    audio_input_tokens: int = 0,
    audio_output_tokens: int = 0,
) -> float:
    p = price_for(model)
    cost = input_tokens * p.input + output_tokens * p.output
    cost += audio_input_tokens * (p.audio_input if p.audio_input is not None else p.input)
    cost += audio_output_tokens * (p.audio_output if p.audio_output is not None else p.output)
    return round(cost / 1_000_000, 8)
