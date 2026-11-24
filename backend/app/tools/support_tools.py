"""The six support tools. Each is a thin adapter: validate -> enforce policy -> call the repo."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.agent.context import TurnContext
from app.agent.events import CustomerUpdate, Escalation
from app.db.seed import hash_pin
from app.tools.base import Tool, ToolRegistry, ToolResult
from app.tools.policy import REFUND_RULES, UPDATABLE_FIELDS, evaluate_refund, invoice_eligibility

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


# ---------------------------------------------------------------------------------------
# 1. Identity verification
# ---------------------------------------------------------------------------------------
class VerifyArgs(BaseModel):
    email: str = Field(description="Email address on the customer's account.")
    pin: str = Field(description="The customer's 4-digit support PIN.")


class VerifyCustomerTool(Tool[VerifyArgs]):
    name = "verify_customer"
    description = (
        "Verify the customer's identity with their account email and 4-digit support PIN. Must succeed before "
        "any account-specific action. Returns the customer's subscription tier and contract status."
    )
    label = "Verifying customer identity..."
    args_model = VerifyArgs

    async def run(self, ctx: TurnContext, args: VerifyArgs) -> ToolResult:
        sessions, crm, s = ctx.deps.sessions, ctx.deps.crm, ctx.deps.settings
        session = await sessions.get(ctx.session_id)
        if session and session.failed_verifications >= s.max_verification_attempts:
            return ToolResult(
                ok=False,
                blocked=True,
                summary="Verification locked",
                data={
                    "verified": False,
                    "error": "verification_locked",
                    "message": "Too many failed attempts. Do not ask for the PIN again; create a high-priority "
                    "'security' escalation ticket.",
                },
            )
        customer = await crm.customer_by_email(args.email)
        pin = re.sub(r"\D", "", args.pin)
        if customer is None or customer.pin_hash != hash_pin(pin):
            failures = await sessions.record_failed_verification(ctx.session_id)
            remaining = max(0, s.max_verification_attempts - failures)
            return ToolResult(
                ok=False,
                blocked=True,
                summary=f"Verification failed ({remaining} attempts left)",
                data={
                    "verified": False,
                    "error": "verification_failed",
                    "attempts_remaining": remaining,
                    "message": "Email or PIN did not match. Do not reveal which one was wrong.",
                },
            )
        await sessions.set_verified(ctx.session_id, customer.id)
        ctx.customer = customer
        view = customer.public_view()
        await ctx.emit(CustomerUpdate(customer=view))
        ctx.add_evidence("verify_customer", view)
        return ToolResult(
            ok=True,
            summary=f"Verified {customer.name} - {customer.tier} tier, contract {customer.contract_status}",
            data={"verified": True, **view},
        )


# ---------------------------------------------------------------------------------------
# 2. Account, tier & contract overview
# ---------------------------------------------------------------------------------------
class NoArgs(BaseModel):
    pass


class AccountOverviewTool(Tool[NoArgs]):
    name = "get_account_overview"
    description = (
        "Get the verified customer's subscription tier, contract status and end date, tier refund limits, and "
        "recent invoices with how much of each is refundable right now under policy."
    )
    label = "Checking account tier & contract status..."
    args_model = NoArgs
    requires_verification = True

    async def run(self, ctx: TurnContext, args: NoArgs) -> ToolResult:
        assert ctx.customer is not None
        customer = await ctx.deps.crm.customer(ctx.customer.id) or ctx.customer
        ctx.customer = customer
        invoices = await ctx.deps.crm.invoices(customer.id)
        rule = REFUND_RULES.get(customer.tier)
        data: dict[str, Any] = {
            **customer.public_view(),
            "refund_policy": (
                {"per_refund_limit": _money(rule.max_cents), "window_days": rule.window_days}
                if rule
                else "not eligible for refunds"
            ),
            "invoices": [invoice_eligibility(customer, inv) for inv in invoices],
        }
        ctx.add_evidence("get_account_overview", data)
        return ToolResult(
            ok=True,
            summary=f"{customer.tier.title()} tier, contract {customer.contract_status}, {len(invoices)} invoices",
            data=data,
        )


# ---------------------------------------------------------------------------------------
# 3. Knowledge base retrieval (tier-aware) with confidence guardrail
# ---------------------------------------------------------------------------------------
class SearchArgs(BaseModel):
    query: str = Field(description="A focused, self-contained search query describing the policy or problem.")


class SearchKnowledgeBaseTool(Tool[SearchArgs]):
    name = "search_knowledge_base"
    description = (
        "Search Nimbus Cloud's support policies and troubleshooting guides. Results are filtered to the verified "
        "customer's tier. Always use this before answering any policy, billing, plan or troubleshooting question."
    )
    label = "Searching knowledge base..."
    args_model = SearchArgs

    async def run(self, ctx: TurnContext, args: SearchArgs) -> ToolResult:
        tier = ctx.customer.tier if ctx.customer else None
        with ctx.trace.span("retrieval", "retrieval", query=args.query, tier=tier) as span:
            result = await ctx.deps.kb.search(args.query, tier=tier, trace=ctx.trace)
            span.attrs.update(
                band=result.band,
                top_similarity=round(result.top_similarity, 4),
                hits=[
                    {"citation": c.citation(), "similarity": round(c.similarity, 4), "rrf": round(c.rrf_score, 4)}
                    for c in result.chunks
                ],
            )
        ctx.retrievals.append(result)
        if result.band == "low":
            return ToolResult(
                ok=True,
                summary=f"No reliable match (best similarity {result.top_similarity:.2f})",
                data={
                    "confidence": "low",
                    "top_similarity": round(result.top_similarity, 3),
                    "results": [],
                    "guidance": "NO_RELIABLE_POLICY_FOUND. Do not answer from general knowledge. You may retry "
                    "once with a rephrased query. If still nothing: for a Nimbus Cloud support question, "
                    "tell the customer you can't confirm it from official documentation and that it is "
                    "being routed to a human specialist. The ticket is opened automatically and its ID is "
                    "appended to your reply - do not call create_escalation_ticket, ask permission, or "
                    "mention how tickets are created. If "
                    "the request is unrelated to Nimbus Cloud, politely say it is outside what you can help with.",
                },
            )
        usable = [c for c in result.chunks if c.similarity >= ctx.deps.settings.rag_low_confidence] or result.chunks[:2]
        for c in usable:
            ctx.add_evidence(c.citation(), c.content)
        return ToolResult(
            ok=True,
            summary=f"{len(usable)} passages, {result.band} confidence (best {result.top_similarity:.2f})",
            data={
                "confidence": result.band,
                "top_similarity": round(result.top_similarity, 3),
                "tier_filter": tier or "none (customer not verified - general policy only)",
                "results": [
                    {
                        "citation": c.citation(),
                        "section": c.section_path,
                        "similarity": round(c.similarity, 3),
                        "content": c.content,
                    }
                    for c in usable
                ],
                "guidance": "Answer only from these passages and cite them like [refund-policy#Section]."
                + (
                    " Confidence is medium: answer cautiously and only what the passages state."
                    if result.band == "medium"
                    else ""
                ),
            },
        )


# ---------------------------------------------------------------------------------------
# 4. Simulated action: refund within policy limits
# ---------------------------------------------------------------------------------------
class RefundArgs(BaseModel):
    invoice_id: str = Field(description="Invoice ID, e.g. INV-9003.")
    amount: float = Field(description="Refund amount in USD.", gt=0)
    reason: str = Field(description="Short reason for the refund, in the customer's words.")


class IssueRefundTool(Tool[RefundArgs]):
    name = "issue_refund"
    description = (
        "Issue a refund on one of the verified customer's invoices. Policy limits (tier per-refund limit, refund "
        "window, active contract) are enforced by the billing system; if blocked, explain why and offer escalation. "
        "Confirm the invoice and amount with the customer before calling."
    )
    label = "Processing refund..."
    args_model = RefundArgs
    requires_verification = True
    side_effect = True

    async def run(self, ctx: TurnContext, args: RefundArgs) -> ToolResult:
        assert ctx.customer is not None
        crm = ctx.deps.crm
        customer = await crm.customer(ctx.customer.id) or ctx.customer
        invoice = await crm.invoice(customer.id, args.invoice_id)
        if invoice is None:
            return ToolResult(
                ok=False,
                blocked=True,
                summary="Invoice not found",
                data={
                    "refunded": False,
                    "error": "invoice_not_found",
                    "message": f"No invoice {args.invoice_id} on this account.",
                },
            )
        amount_cents = round(args.amount * 100)
        decision = evaluate_refund(customer, invoice, amount_cents)
        ctx.trace.spans[-1].attrs["policy_decision"] = decision.code
        if not decision.allowed:
            return ToolResult(
                ok=False,
                blocked=True,
                summary=f"Blocked by policy: {decision.code}",
                data={
                    "refunded": False,
                    "policy_code": decision.code,
                    "message": decision.message,
                    "requires_escalation": decision.code not in {"invalid_amount", "already_refunded"},
                },
            )
        try:
            refund_id = await crm.apply_refund(customer.id, invoice.id, amount_cents, args.reason, ctx.session_id)
        except ValueError as exc:
            return ToolResult(
                ok=False, blocked=True, summary="Refund rejected", data={"refunded": False, "message": str(exc)}
            )
        data = {
            "refunded": True,
            "refund_id": refund_id,
            "invoice_id": invoice.id,
            "amount": _money(amount_cents),
            "processing_time": "5-10 business days to the original payment method",
        }
        ctx.add_evidence("issue_refund", data)
        return ToolResult(ok=True, summary=f"Refunded {_money(amount_cents)} on {invoice.id} ({refund_id})", data=data)


# ---------------------------------------------------------------------------------------
# 5. Simulated action: update an account field
# ---------------------------------------------------------------------------------------
class UpdateAccountArgs(BaseModel):
    field: Literal["billing_email", "notification_pref", "phone", "company"] = Field(
        description="Field to update: " + "; ".join(f"{k} = {v}" for k, v in UPDATABLE_FIELDS.items())
    )
    value: str = Field(description="New value for the field.")

    @field_validator("value")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 120:
            raise ValueError("value must be 1-120 characters")
        return v


class UpdateAccountTool(Tool[UpdateAccountArgs]):
    name = "update_account"
    description = "Update one field on the verified customer's account. Confirm the new value with the customer first."
    label = "Updating account details..."
    args_model = UpdateAccountArgs
    requires_verification = True
    side_effect = True

    async def run(self, ctx: TurnContext, args: UpdateAccountArgs) -> ToolResult:
        assert ctx.customer is not None
        value = args.value
        if args.field == "billing_email" and not _EMAIL.match(value):
            return ToolResult(
                ok=False,
                blocked=True,
                summary="Invalid email",
                data={"updated": False, "message": "That is not a valid email address."},
            )
        if args.field == "notification_pref":
            value = value.lower()
            if value not in {"email", "sms", "none"}:
                return ToolResult(
                    ok=False,
                    blocked=True,
                    summary="Invalid preference",
                    data={"updated": False, "message": "Must be email, sms or none."},
                )
        if args.field == "phone" and not re.fullmatch(r"\+?[\d\s\-()]{7,20}", value):
            return ToolResult(
                ok=False,
                blocked=True,
                summary="Invalid phone",
                data={"updated": False, "message": "That is not a valid phone number."},
            )
        old, new = await ctx.deps.crm.update_field(ctx.customer.id, args.field, value, ctx.session_id)
        refreshed = await ctx.deps.crm.customer(ctx.customer.id)
        if refreshed:
            ctx.customer = refreshed
            await ctx.emit(CustomerUpdate(customer=refreshed.public_view()))
        data = {"updated": True, "field": args.field, "old_value": old, "new_value": new}
        ctx.add_evidence("update_account", data)
        return ToolResult(ok=True, summary=f"Updated {args.field}", data=data)


# ---------------------------------------------------------------------------------------
# 6. Escalation to a human
# ---------------------------------------------------------------------------------------
class EscalationArgs(BaseModel):
    category: Literal["billing", "technical", "security", "account", "contracts", "general"]
    priority: Literal["low", "medium", "high", "urgent"]
    reason: str = Field(description="Why a human is needed (e.g. 'refund above tier limit', 'no KB answer').")
    summary: str = Field(description="Concise case summary so the customer does not have to repeat themselves.")


class CreateEscalationTicketTool(Tool[EscalationArgs]):
    name = "create_escalation_ticket"
    description = (
        "Hand the case to a human support team by creating a structured escalation ticket. Use when policy blocks "
        "an action, the knowledge base has no reliable answer, verification is locked, a security issue is "
        "reported, a system is down, or the customer asks for a human."
    )
    label = "Creating escalation ticket..."
    args_model = EscalationArgs
    side_effect = True

    async def run(self, ctx: TurnContext, args: EscalationArgs) -> ToolResult:
        ticket = await ctx.deps.crm.create_ticket(
            customer_id=ctx.customer.id if ctx.customer else None,
            session_id=ctx.session_id,
            category=args.category,
            priority=args.priority,
            reason=args.reason,
            summary=args.summary,
        )
        await ctx.deps.sessions.set_status(ctx.session_id, "escalated")
        ctx.tickets.append(ticket)
        payload = ticket.model_dump(mode="json")
        await ctx.emit(Escalation(ticket=payload))
        ctx.add_evidence("create_escalation_ticket", payload)
        return ToolResult(
            ok=True,
            summary=f"Ticket {ticket.id} ({args.category}, {args.priority})",
            data={"created": True, "ticket_id": ticket.id, "category": ticket.category, "priority": ticket.priority},
        )


def build_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            VerifyCustomerTool(),
            AccountOverviewTool(),
            SearchKnowledgeBaseTool(),
            IssueRefundTool(),
            UpdateAccountTool(),
            CreateEscalationTicketTool(),
        ]
    )
