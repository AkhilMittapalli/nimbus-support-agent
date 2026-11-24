"""Deterministic mock CRM data. Idempotent: existing rows are left untouched.

Invoice dates are relative to "today" so refund-window rules always behave the same in a demo.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta

import asyncpg
import structlog

log = structlog.get_logger(__name__)

_PIN_SALT = "nimbus-demo-salt"


def hash_pin(pin: str) -> str:
    return hashlib.sha256(f"{_PIN_SALT}:{pin.strip()}".encode()).hexdigest()


@dataclass(frozen=True)
class DemoCustomer:
    id: str
    name: str
    email: str
    pin: str
    company: str | None
    tier: str
    contract_status: str
    contract_days_left: int | None
    scenario: str


DEMO_CUSTOMERS: list[DemoCustomer] = [
    DemoCustomer(
        "cus_1001",
        "Alice Chen",
        "alice@acme.io",
        "1234",
        "Acme Robotics",
        "enterprise",
        "active",
        240,
        "Enterprise, active contract: large refunds allowed (up to $1,000), priority support.",
    ),
    DemoCustomer(
        "cus_1002",
        "Bob Martinez",
        "bob@brightlabs.dev",
        "2222",
        "Bright Labs",
        "pro",
        "active",
        120,
        "Pro, active: refunds up to $200 within 30 days; one invoice is 45 days old (outside window).",
    ),
    DemoCustomer(
        "cus_1003",
        "Carol Singh",
        "carol@example.com",
        "3333",
        None,
        "starter",
        "active",
        20,
        "Starter, active: refunds up to $50 within 14 days.",
    ),
    DemoCustomer(
        "cus_1004",
        "Dan Okafor",
        "dan@okafor.co",
        "4444",
        "Okafor & Co",
        "pro",
        "past_due",
        60,
        "Pro but past_due: refunds blocked until the balance is settled -> escalation path.",
    ),
    DemoCustomer(
        "cus_1005",
        "Eve Laurent",
        "eve@example.com",
        "5555",
        None,
        "free",
        "active",
        None,
        "Free tier: no paid invoices, community support only.",
    ),
]

# (invoice_id, customer_id, description, amount_cents, days_ago, refunded_cents)
_INVOICES: list[tuple[str, str, str, int, int, int]] = [
    ("INV-9001", "cus_1001", "Enterprise annual add-on: 25 seats", 450_00, 12, 0),
    ("INV-9002", "cus_1001", "Enterprise monthly platform fee", 2_400_00, 40, 0),
    ("INV-9003", "cus_1002", "Pro plan - monthly", 149_00, 9, 0),
    ("INV-9004", "cus_1002", "Pro plan - monthly", 149_00, 45, 0),
    ("INV-9005", "cus_1003", "Starter plan - monthly", 29_00, 5, 0),
    ("INV-9006", "cus_1003", "Starter plan - extra storage", 79_00, 20, 0),
    ("INV-9007", "cus_1004", "Pro plan - monthly", 149_00, 10, 0),
]


async def seed(pool: asyncpg.Pool) -> None:
    today = date.today()
    async with pool.acquire() as conn, conn.transaction():
        for c in DEMO_CUSTOMERS:
            contract_end = today + timedelta(days=c.contract_days_left) if c.contract_days_left else None
            await conn.execute(
                """
                INSERT INTO customers (id, name, email, pin_hash, company, tier, contract_status,
                                       contract_end, billing_email)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $3)
                ON CONFLICT (id) DO NOTHING
                """,
                c.id,
                c.name,
                c.email,
                hash_pin(c.pin),
                c.company,
                c.tier,
                c.contract_status,
                contract_end,
            )
        for inv_id, cus_id, desc, amount, days_ago, refunded in _INVOICES:
            await conn.execute(
                """
                INSERT INTO invoices (id, customer_id, description, amount_cents, refunded_cents, issued_on)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (id) DO NOTHING
                """,
                inv_id,
                cus_id,
                desc,
                amount,
                refunded,
                today - timedelta(days=days_ago),
            )
    log.info("db.seeded", customers=len(DEMO_CUSTOMERS), invoices=len(_INVOICES))


async def reset_demo_data(pool: asyncpg.Pool) -> None:
    """Restore mock CRM data to its pristine state (used by the demo 'reset' endpoint)."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM refunds")
        await conn.execute("DELETE FROM account_changes")
        await conn.execute("DELETE FROM tickets")
        await conn.execute("DELETE FROM invoices")
        await conn.execute("UPDATE sessions SET verified_customer_id = NULL")
        await conn.execute("DELETE FROM customers")
    await seed(pool)
