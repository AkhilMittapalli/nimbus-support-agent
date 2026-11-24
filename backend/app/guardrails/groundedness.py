"""Secondary verification: is the drafted answer supported by the evidence gathered this turn?

Two independent signals are combined into one confidence score:
  * retrieval confidence - cosine similarity band of the best KB hit (pre-generation gate, enforced in
    the search tool: low-confidence retrievals return no passages at all), and
  * groundedness - an LLM judge (Flash-Lite, JSON schema output) checks every factual claim in the draft
    against the turn's evidence (KB passages + tool outputs).

The final action is "answer", "flagged" (verifier unavailable - fail open, but marked), "retracted"
(ungrounded - the streamed answer is replaced and the case escalated) or "escalated".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from app.agent.context import Evidence
from app.config import Settings
from app.errors import AppError
from app.llm.gemini import GeminiClient
from app.observability.tracer import TurnTrace

log = structlog.get_logger(__name__)

Band = Literal["high", "medium", "low", "none"]
OFF_TOPIC = "off_topic: reply engaged with a request outside Nimbus Cloud support"
_BAND_SCORE: dict[str, float] = {"high": 1.0, "medium": 0.75, "low": 0.3}


class UnsupportedClaim(BaseModel):
    claim: str
    material: bool = Field(
        description="true if a customer could be misled or harmed by it: amounts, limits, time frames, dates, IDs, "
        "eligibility, policy rules, promises or action outcomes. false for cosmetic labels, wording or formatting."
    )


class GroundednessJudgement(BaseModel):
    performs_unrelated_task: bool = Field(
        description="true ONLY if the reply actually produces content for a request unrelated to Nimbus Cloud "
        "support - e.g. it writes the poem, tells the joke, answers the trivia or writes the code. A reply that "
        "declines, refuses or redirects to Nimbus Cloud topics is false."
    )
    kind: Literal["conversational", "factual"] = Field(
        description="'conversational' if the reply only greets, asks for information, confirms an action, or "
        "declines; 'factual' if it states policy, numbers, procedures or account facts."
    )
    unsupported_claims: list[UnsupportedClaim] = Field(
        description="Factual claims in the reply NOT supported by the evidence."
    )
    score: float = Field(
        ge=0,
        le=1,
        description="Fraction of MATERIAL factual claims that are supported (1.0 if there are none). Minor "
        "cosmetic mismatches must not lower this score.",
    )


@dataclass
class GuardrailOutcome:
    retrieval_band: Band
    top_similarity: float | None
    verdict: Literal["grounded", "ungrounded", "skipped", "error"]
    groundedness: float | None
    confidence: float
    action: Literal["answer", "retracted", "escalated", "flagged"]
    reasons: list[str] = field(default_factory=list)


_JUDGE_SYSTEM = (
    "You are a strict fact-checking auditor for a customer-support assistant. You receive EVIDENCE (knowledge "
    "base passages and system/tool outputs) and a REPLY. Identify every factual claim in the REPLY (policy "
    "rules, limits, amounts, time frames, procedures, account facts, ticket or refund IDs). A claim is supported "
    "only if the EVIDENCE states it or it follows directly from it - simple arithmetic, comparisons against a "
    "stated limit, and logical consequences of stated rules count as supported. Restating the customer's own "
    "request, general courtesy, questions to the customer, refusals to break policy, and offers to escalate are "
    "not factual claims. Be strict about numbers, time frames and IDs that do not appear in the EVIDENCE."
)


def _needs_check(answer: str) -> bool:
    text = answer.strip()
    return len(text) >= 40 or bool(re.search(r"\d", text))


async def check_groundedness(
    *,
    llm: GeminiClient,
    settings: Settings,
    trace: TurnTrace,
    answer: str,
    evidence: list[Evidence],
    retrieval_band: Band,
    top_similarity: float | None,
) -> GuardrailOutcome:
    reasons: list[str] = []
    retrieval_score = _BAND_SCORE.get(retrieval_band)

    if not settings.groundedness_enabled or not _needs_check(answer):
        conf = retrieval_score if retrieval_score is not None else 1.0
        return GuardrailOutcome(
            retrieval_band,
            top_similarity,
            "skipped",
            None,
            conf,
            "answer",
            ["short conversational reply"] if settings.groundedness_enabled else ["disabled"],
        )

    evidence_text = "\n\n".join(f"[{i + 1}] ({e.source}) {e.content[:1500]}" for i, e in enumerate(evidence[-12:]))
    prompt = (
        f"EVIDENCE:\n{evidence_text or '(none - no tools or knowledge base were used this turn)'}\n\nREPLY:\n{answer}"
    )
    with trace.span("guardrail.groundedness", "guardrail", retrieval_band=retrieval_band) as span:
        try:
            judgement = await llm.generate_json(
                model=settings.verifier_model,
                prompt=prompt,
                schema=GroundednessJudgement,
                trace=trace,
                span_name="llm.verifier",
                timeout_s=settings.verifier_timeout_s,
                system_instruction=_JUDGE_SYSTEM,
            )
        except AppError as exc:
            log.warning("guardrail.verifier_unavailable", code=exc.code)
            span.status = "error"
            conf = min(retrieval_score or 0.6, 0.6)
            return GuardrailOutcome(
                retrieval_band,
                top_similarity,
                "error",
                None,
                conf,
                "flagged",
                [f"verifier unavailable ({exc.code}); answer shown unverified"],
            )

        if judgement.performs_unrelated_task:
            span.status = "blocked"
            span.attrs.update(kind="off_topic")
            return GuardrailOutcome(retrieval_band, top_similarity, "ungrounded", None, 0.0, "retracted", [OFF_TOPIC])

        material = [c.claim for c in judgement.unsupported_claims if c.material]
        minor = [c.claim for c in judgement.unsupported_claims if not c.material]
        span.attrs.update(
            kind=judgement.kind, score=judgement.score, unsupported_material=material, unsupported_minor=minor
        )
        if judgement.kind == "conversational" and not material:
            conf = retrieval_score if retrieval_score is not None else 1.0
            return GuardrailOutcome(retrieval_band, top_similarity, "grounded", 1.0, conf, "answer", ["conversational"])

        # Retract only for material unsupported claims (or a low overall score); cosmetic mismatches
        # such as a paraphrased ticket category are recorded but don't destroy a correct answer.
        grounded = judgement.score >= settings.groundedness_min_score and not material
        components = [judgement.score] + ([retrieval_score] if retrieval_score is not None else [])
        confidence = round(min(components), 3)
        reasons.extend(f"unsupported: {c}" for c in material[:5])
        reasons.extend(f"minor (not blocking): {c}" for c in minor[:3])
        if grounded:
            return GuardrailOutcome(
                retrieval_band, top_similarity, "grounded", judgement.score, confidence, "answer", reasons
            )
        span.status = "blocked"
        return GuardrailOutcome(
            retrieval_band, top_similarity, "ungrounded", judgement.score, confidence, "retracted", reasons
        )
