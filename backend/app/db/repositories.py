"""Typed data-access layer. All SQL lives here; the rest of the app works with pydantic models."""

from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel

Tier = Literal["free", "starter", "pro", "enterprise"]
Mode = Literal["text", "voice"]


# ---------------------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------------------
class Customer(BaseModel):
    id: str
    name: str
    email: str
    pin_hash: str
    company: str | None
    tier: Tier
    contract_status: str
    contract_end: date | None
    billing_email: str
    notification_pref: str
    phone: str | None

    def public_view(self) -> dict[str, Any]:
        return {
            "customer_id": self.id,
            "name": self.name,
            "email": self.email,
            "company": self.company,
            "tier": self.tier,
            "contract_status": self.contract_status,
            "contract_end": self.contract_end.isoformat() if self.contract_end else None,
            "billing_email": self.billing_email,
            "notification_pref": self.notification_pref,
            "phone": self.phone,
        }


class Invoice(BaseModel):
    id: str
    customer_id: str
    description: str
    amount_cents: int
    refunded_cents: int
    issued_on: date
    status: str

    @property
    def refundable_cents(self) -> int:
        return self.amount_cents - self.refunded_cents


class Session(BaseModel):
    id: uuid.UUID
    verified_customer_id: str | None
    status: Literal["active", "escalated", "closed"]
    failed_verifications: int
    turn_count: int
    created_at: datetime


class Message(BaseModel):
    id: int
    turn_id: str
    role: Literal["user", "assistant"]
    mode: Mode
    content: str
    meta: dict[str, Any]
    created_at: datetime


class Ticket(BaseModel):
    id: str
    customer_id: str | None
    category: str
    priority: str
    reason: str
    summary: str
    status: str
    created_at: datetime


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3).upper()}"


# ---------------------------------------------------------------------------------------
# Sessions & conversation memory
# ---------------------------------------------------------------------------------------
class SessionRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_or_create(self, session_id: uuid.UUID) -> Session:
        row = await self.pool.fetchrow(
            """
            INSERT INTO sessions (id) VALUES ($1)
            ON CONFLICT (id) DO UPDATE SET updated_at = now()
            RETURNING *
            """,
            session_id,
        )
        return Session(**dict(row))

    async def get(self, session_id: uuid.UUID) -> Session | None:
        row = await self.pool.fetchrow("SELECT * FROM sessions WHERE id = $1", session_id)
        return Session(**dict(row)) if row else None

    async def set_verified(self, session_id: uuid.UUID, customer_id: str) -> None:
        await self.pool.execute(
            "UPDATE sessions SET verified_customer_id = $2, failed_verifications = 0, updated_at = now() WHERE id = $1",
            session_id,
            customer_id,
        )

    async def record_failed_verification(self, session_id: uuid.UUID) -> int:
        return int(
            await self.pool.fetchval(
                "UPDATE sessions SET failed_verifications = failed_verifications + 1, updated_at = now() "
                "WHERE id = $1 RETURNING failed_verifications",
                session_id,
            )
        )

    async def set_status(self, session_id: uuid.UUID, status: str) -> None:
        await self.pool.execute("UPDATE sessions SET status = $2, updated_at = now() WHERE id = $1", session_id, status)

    async def increment_turns(self, session_id: uuid.UUID) -> int:
        return int(
            await self.pool.fetchval(
                "UPDATE sessions SET turn_count = turn_count + 1, updated_at = now() WHERE id = $1 "
                "RETURNING turn_count",
                session_id,
            )
        )

    async def add_message(
        self,
        session_id: uuid.UUID,
        turn_id: str,
        role: str,
        mode: Mode,
        content: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        await self.pool.execute(
            "INSERT INTO messages (session_id, turn_id, role, mode, content, meta) VALUES ($1, $2, $3, $4, $5, $6)",
            session_id,
            turn_id,
            role,
            mode,
            content,
            meta or {},
        )

    async def history(self, session_id: uuid.UUID, limit: int) -> list[Message]:
        rows = await self.pool.fetch(
            """
            SELECT * FROM (
                SELECT id, turn_id, role, mode, content, meta, created_at FROM messages
                WHERE session_id = $1 ORDER BY id DESC LIMIT $2
            ) recent ORDER BY id ASC
            """,
            session_id,
            limit,
        )
        return [Message(**dict(r)) for r in rows]

    async def add_trace(self, session_id: uuid.UUID, turn_id: str, mode: Mode, data: dict[str, Any]) -> None:
        await self.pool.execute(
            "INSERT INTO traces (session_id, turn_id, mode, data) VALUES ($1, $2, $3, $4)",
            session_id,
            turn_id,
            mode,
            data,
        )

    async def traces(self, session_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT data FROM traces WHERE session_id = $1 ORDER BY id DESC LIMIT $2", session_id, limit
        )
        return [r["data"] for r in reversed(rows)]


# ---------------------------------------------------------------------------------------
# Mock CRM / billing / ticketing
# ---------------------------------------------------------------------------------------
class CrmRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def customer_by_email(self, email: str) -> Customer | None:
        row = await self.pool.fetchrow("SELECT * FROM customers WHERE lower(email) = lower($1)", email.strip())
        return Customer(**_strip(dict(row), "created_at")) if row else None

    async def customer(self, customer_id: str) -> Customer | None:
        row = await self.pool.fetchrow("SELECT * FROM customers WHERE id = $1", customer_id)
        return Customer(**_strip(dict(row), "created_at")) if row else None

    async def invoices(self, customer_id: str) -> list[Invoice]:
        rows = await self.pool.fetch(
            "SELECT * FROM invoices WHERE customer_id = $1 ORDER BY issued_on DESC", customer_id
        )
        return [Invoice(**dict(r)) for r in rows]

    async def invoice(self, customer_id: str, invoice_id: str) -> Invoice | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM invoices WHERE customer_id = $1 AND upper(id) = upper($2)", customer_id, invoice_id.strip()
        )
        return Invoice(**dict(row)) if row else None

    async def refunds_in_window(self, customer_id: str, days: int) -> int:
        return int(
            await self.pool.fetchval(
                "SELECT COALESCE(SUM(amount_cents), 0) FROM refunds "
                "WHERE customer_id = $1 AND created_at > now() - make_interval(days => $2)",
                customer_id,
                days,
            )
        )

    async def apply_refund(
        self, customer_id: str, invoice_id: str, amount_cents: int, reason: str, session_id: uuid.UUID
    ) -> str:
        refund_id = _new_id("RF")
        async with self.pool.acquire() as conn, conn.transaction():
            # Row lock prevents a concurrent double-refund of the same invoice.
            row = await conn.fetchrow(
                "SELECT amount_cents, refunded_cents FROM invoices WHERE id = $1 AND customer_id = $2 FOR UPDATE",
                invoice_id,
                customer_id,
            )
            if row is None or row["amount_cents"] - row["refunded_cents"] < amount_cents:
                raise ValueError("invoice no longer has enough refundable balance")
            new_refunded = row["refunded_cents"] + amount_cents
            status = "refunded" if new_refunded == row["amount_cents"] else "partially_refunded"
            await conn.execute(
                "UPDATE invoices SET refunded_cents = $2, status = $3 WHERE id = $1", invoice_id, new_refunded, status
            )
            await conn.execute(
                "INSERT INTO refunds (id, invoice_id, customer_id, session_id, amount_cents, reason) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                refund_id,
                invoice_id,
                customer_id,
                session_id,
                amount_cents,
                reason,
            )
        return refund_id

    async def update_field(
        self, customer_id: str, field: str, value: str, session_id: uuid.UUID
    ) -> tuple[str | None, str]:
        # `field` is validated against an allow-list by the tool before reaching here.
        if field not in {"billing_email", "notification_pref", "phone", "company"}:
            raise ValueError(f"field {field!r} is not updatable")
        async with self.pool.acquire() as conn, conn.transaction():
            old = await conn.fetchval(f"SELECT {field} FROM customers WHERE id = $1 FOR UPDATE", customer_id)
            await conn.execute(f"UPDATE customers SET {field} = $2 WHERE id = $1", customer_id, value)
            await conn.execute(
                "INSERT INTO account_changes (customer_id, session_id, field, old_value, new_value) "
                "VALUES ($1, $2, $3, $4, $5)",
                customer_id,
                session_id,
                field,
                old,
                value,
            )
        return old, value

    async def create_ticket(
        self,
        *,
        customer_id: str | None,
        session_id: uuid.UUID,
        category: str,
        priority: str,
        reason: str,
        summary: str,
    ) -> Ticket:
        row = await self.pool.fetchrow(
            """
            INSERT INTO tickets (id, customer_id, session_id, category, priority, reason, summary)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, customer_id, category, priority, reason, summary, status, created_at
            """,
            _new_id("TKT"),
            customer_id,
            session_id,
            category,
            priority,
            reason,
            summary,
        )
        return Ticket(**dict(row))

    async def open_tickets(self, session_id: uuid.UUID) -> list[Ticket]:
        rows = await self.pool.fetch(
            "SELECT id, customer_id, category, priority, reason, summary, status, created_at FROM tickets "
            "WHERE session_id = $1 AND status = 'open' ORDER BY created_at",
            session_id,
        )
        return [Ticket(**dict(r)) for r in rows]


def _strip(d: dict[str, Any], *keys: str) -> dict[str, Any]:
    for k in keys:
        d.pop(k, None)
    return d
