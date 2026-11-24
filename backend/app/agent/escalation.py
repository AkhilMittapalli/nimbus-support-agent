"""Deterministic human hand-off, used by guardrails when the model must not be trusted to decide."""

from __future__ import annotations

import structlog

from app.agent.context import TurnContext
from app.agent.events import Escalation

log = structlog.get_logger(__name__)


async def auto_escalate(
    ctx: TurnContext,
    *,
    reason: str,
    summary: str,
    category: str = "general",
    priority: str = "medium",
) -> str:
    """Create (at most one per turn) escalation ticket and move the session to the escalated state.

    Returns the ticket id, or "pending" if the ticketing system itself is unavailable - callers still
    give the customer a safe answer in that case.
    """
    if ctx.tickets:
        return ctx.tickets[-1].id
    try:
        ticket = await ctx.deps.crm.create_ticket(
            customer_id=ctx.customer.id if ctx.customer else None,
            session_id=ctx.session_id,
            category=category,
            priority=priority,
            reason=reason,
            summary=summary[:1000],
        )
        await ctx.deps.sessions.set_status(ctx.session_id, "escalated")
    except Exception:
        log.exception("escalation.auto_failed", reason=reason)
        return "pending"
    ctx.tickets.append(ticket)
    await ctx.emit(Escalation(ticket=ticket.model_dump(mode="json")))
    log.info("escalation.auto_created", ticket=ticket.id, reason=reason)
    return ticket.id
