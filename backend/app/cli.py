"""Operational CLI.

uv run python -m app.cli migrate      # apply schema
uv run python -m app.cli seed         # load mock CRM data
uv run python -m app.cli ingest [--force]
uv run python -m app.cli calibrate    # similarity distribution for in-scope vs out-of-scope queries
"""

from __future__ import annotations

import argparse
import asyncio
import statistics

from app.config import get_settings
from app.container import POLICY_DIR
from app.db.pool import create_pool, migrate
from app.db.seed import seed
from app.llm.gemini import GeminiClient
from app.main import configure_logging
from app.rag.store import KnowledgeStore

IN_SCOPE = [
    "How much can I get refunded on the pro plan?",
    "what is the refund window for starter customers",
    "my files are not syncing on my laptop",
    "how do I reset my password",
    "I lost my 2FA phone and backup codes",
    "what is the uptime SLA for enterprise and how do credits work",
    "can I cancel my subscription anytime",
    "SSO login says invalid SAML response",
    "what happens when my payment fails",
    "API rate limit for pro plan",
    "how do I delete my account",
    "do you offer data residency in the EU",
]
OUT_OF_SCOPE = [
    "what's the weather in Paris tomorrow",
    "write me a poem about the ocean",
    "who won the world cup in 2022",
    "do you sell office furniture",
    "can you help me with my tax return",
    "how do I cook pasta carbonara",
    "what is the capital of Australia",
    "do you integrate with Salesforce CPQ quoting",
    "what's your stock price",
    "do you have a mobile game",
]


async def _calibrate() -> None:
    settings = get_settings()
    pool = await create_pool(settings)
    kb = KnowledgeStore(pool, GeminiClient(settings), settings)
    try:

        async def top(q: str) -> float:
            return (await kb.search(q, tier=None)).top_similarity

        ins = [await top(q) for q in IN_SCOPE]
        outs = [await top(q) for q in OUT_OF_SCOPE]
        for label, qs, scores in (("IN-SCOPE", IN_SCOPE, ins), ("OUT-OF-SCOPE", OUT_OF_SCOPE, outs)):
            print(f"\n{label}")
            for q, sc in sorted(zip(qs, scores, strict=True), key=lambda x: x[1]):
                print(f"  {sc:.3f}  {q}")
        print(f"\nin-scope  min={min(ins):.3f} median={statistics.median(ins):.3f}")
        print(f"out-scope max={max(outs):.3f} median={statistics.median(outs):.3f}")
        print(f"current thresholds: low<{settings.rag_low_confidence} high>={settings.rag_high_confidence}")
        gap_mid = (min(ins) + max(outs)) / 2
        print(f"suggested RAG_LOW_CONFIDENCE ~ {gap_mid:.2f} (midpoint of the separation gap)")
    finally:
        await pool.close()


async def _run(cmd: str, force: bool) -> None:
    settings = get_settings()
    configure_logging(settings)
    if cmd == "calibrate":
        await _calibrate()
        return
    pool = await create_pool(settings)
    try:
        if cmd in ("migrate", "ingest"):
            await migrate(pool, settings)
        if cmd == "seed":
            await seed(pool)
        if cmd == "ingest":
            kb = KnowledgeStore(pool, GeminiClient(settings), settings)
            print(await kb.ingest_directory(POLICY_DIR, force=force))
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    parser.add_argument("command", choices=["migrate", "seed", "ingest", "calibrate"])
    parser.add_argument("--force", action="store_true", help="re-embed documents even if unchanged")
    args = parser.parse_args()
    asyncio.run(_run(args.command, args.force))


if __name__ == "__main__":
    main()
