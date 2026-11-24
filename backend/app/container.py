"""Composition root: builds and owns all long-lived dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import asyncpg
import structlog

from app.agent.context import Deps
from app.agent.orchestrator import ConversationService
from app.config import Settings
from app.db.pool import create_pool, migrate
from app.db.repositories import CrmRepo, SessionRepo
from app.db.seed import seed
from app.llm.gemini import GeminiClient
from app.rag.store import KnowledgeStore
from app.tools.support_tools import build_registry

log = structlog.get_logger(__name__)

POLICY_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"


@dataclass
class Container:
    settings: Settings
    pool: asyncpg.Pool
    llm: GeminiClient
    kb: KnowledgeStore
    sessions: SessionRepo
    crm: CrmRepo
    conversation: ConversationService

    @classmethod
    async def build(cls, settings: Settings) -> Container:
        pool = await create_pool(settings)
        if settings.auto_migrate:
            await migrate(pool, settings)
        if settings.auto_seed:
            await seed(pool)
        llm = GeminiClient(settings)
        kb = KnowledgeStore(pool, llm, settings)
        if settings.auto_ingest:
            if settings.gemini_api_key.get_secret_value():
                try:
                    await kb.ingest_directory(POLICY_DIR)
                except Exception:  # serve traffic anyway; /readyz reports the empty KB
                    log.exception("kb.auto_ingest_failed")
            else:
                log.warning("kb.auto_ingest_skipped", reason="GEMINI_API_KEY not set")
        sessions, crm = SessionRepo(pool), CrmRepo(pool)
        deps = Deps(settings=settings, llm=llm, kb=kb, sessions=sessions, crm=crm)
        conversation = ConversationService(deps, build_registry())
        return cls(settings=settings, pool=pool, llm=llm, kb=kb, sessions=sessions, crm=crm, conversation=conversation)

    async def close(self) -> None:
        await self.pool.close()
