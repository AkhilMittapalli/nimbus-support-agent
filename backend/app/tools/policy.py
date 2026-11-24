"""Business rules enforced in code. The knowledge base documents the same rules for customers;
the agent can *explain* policy from RAG but can only *act* within these limits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.db.repositories import Customer, Invoice


@dataclass(frozen=True)
class RefundRule:
    max_cents: int
    window_days: int


REFUND_RULES: dict[str, RefundRule | None] = {
    "free": None,
    "starter": RefundRule(max_cents=50_00, window_days=14),
    "pro": RefundRule(max_cents=200_00, window_days=30),
    "enterprise": RefundRule(max_cents=1_000_00, window_days=60),
}


@dataclass(frozen=True)
class RefundDecision:
    allowed: bool
    code: str
    message: str


def evaluate_refund(
    customer: Customer, invoice: Invoice, amount_cents: int, today: date | None = None
) -> RefundDecision:
    today = today or date.today()
    rule = REFUND_RULES.get(customer.tier)
    if customer.contract_status != "active":
        return RefundDecision(
            False,
            "contract_not_active",
            f"Contract status is '{customer.contract_status}'. Refunds require an active contract; "
            "escalate to the Billing team.",
        )
    if rule is None:
        return RefundDecision(False, "tier_not_eligible", f"The {customer.tier} tier has no refundable invoices.")
    if amount_cents <= 0:
        return RefundDecision(False, "invalid_amount", "Refund amount must be greater than zero.")
    if invoice.refundable_cents <= 0:
        return RefundDecision(False, "already_refunded", f"Invoice {invoice.id} has already been fully refunded.")
    if amount_cents > invoice.refundable_cents:
        return RefundDecision(
            False,
            "exceeds_invoice_balance",
            f"Only ${invoice.refundable_cents / 100:.2f} remains refundable on {invoice.id}.",
        )
    age = (today - invoice.issued_on).days
    if age > rule.window_days:
        return RefundDecision(
            False,
            "outside_window",
            f"Invoice {invoice.id} is {age} days old; the {customer.tier} refund window is "
            f"{rule.window_days} days. Escalate to Billing for an exception.",
        )
    if amount_cents > rule.max_cents:
        return RefundDecision(
            False,
            "exceeds_tier_limit",
            f"${amount_cents / 100:.2f} exceeds the {customer.tier} per-refund limit of "
            f"${rule.max_cents / 100:.2f}. Offer the maximum or escalate to Billing.",
        )
    return RefundDecision(True, "approved", "Refund is within policy.")


def invoice_eligibility(customer: Customer, invoice: Invoice, today: date | None = None) -> dict[str, object]:
    today = today or date.today()
    rule = REFUND_RULES.get(customer.tier)
    age = (today - invoice.issued_on).days
    max_now = 0
    if rule and customer.contract_status == "active" and age <= rule.window_days:
        max_now = min(rule.max_cents, invoice.refundable_cents)
    return {
        "invoice_id": invoice.id,
        "description": invoice.description,
        "amount": f"${invoice.amount_cents / 100:.2f}",
        "already_refunded": f"${invoice.refunded_cents / 100:.2f}",
        "issued_on": invoice.issued_on.isoformat(),
        "age_days": age,
        "max_refundable_now": f"${max_now / 100:.2f}",
    }


UPDATABLE_FIELDS = {
    "billing_email": "Billing email address",
    "notification_pref": "Notification preference (email, sms or none)",
    "phone": "Contact phone number",
    "company": "Company name",
}
