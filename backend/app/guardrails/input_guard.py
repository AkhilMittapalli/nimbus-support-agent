"""Cheap pre-LLM input checks. Injection attempts are *flagged* (logged + traced) rather than blocked:
the real defence is architectural - tools take the customer identity from the server-side session,
never from model arguments, and every action is policy-checked in code."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.errors import InputRejectedError

_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (all |any )?(previous|prior|above) (instructions|rules)",
        r"you are now (a|an|in) ",
        r"system prompt",
        r"developer mode",
        r"act as (an? )?(admin|administrator|developer)",
        r"bypass (the )?(policy|limit|verification)",
    )
]


@dataclass
class InputCheck:
    text: str
    flags: list[str] = field(default_factory=list)


def check_input(text: str, *, max_chars: int) -> InputCheck:
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()
    if not cleaned:
        raise InputRejectedError("empty message", user_message="Please type a message.")
    if len(cleaned) > max_chars:
        raise InputRejectedError(
            "message too long", user_message=f"Messages are limited to {max_chars} characters - please shorten it."
        )
    flags = [f"possible_prompt_injection:{p.pattern}" for p in _INJECTION_PATTERNS if p.search(cleaned)]
    return InputCheck(text=cleaned, flags=flags)
