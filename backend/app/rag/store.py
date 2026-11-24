"""pgvector-backed knowledge store: idempotent ingestion + tier-aware hybrid retrieval.

Retrieval = dense (cosine over Gemini embeddings) + sparse (Postgres full-text) candidates, fused
with Reciprocal Rank Fusion. Dense similarity of the best hit is kept separately because it is the
signal the confidence guardrail is calibrated on (RRF scores are rank-based and not comparable
across queries).
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import asyncpg
import structlog

from app.config import Settings
from app.llm.gemini import GeminiClient
from app.observability.tracer import TurnTrace
from app.rag.chunker import PolicyDocument, load_corpus

log = structlog.get_logger(__name__)

ConfidenceBand = Literal["high", "medium", "low"]
_WORD = re.compile(r"[a-z0-9]{2,}")
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "if",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "my",
        "me",
        "i",
        "you",
        "your",
        "our",
        "we",
        "it",
        "this",
        "that",
        "can",
        "could",
        "would",
        "should",
        "do",
        "does",
        "did",
        "how",
        "what",
        "when",
        "where",
        "why",
        "which",
        "who",
        "have",
        "has",
        "had",
        "will",
        "just",
        "about",
        "from",
    ]
)
_RRF_K = 60


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def keyword_tsquery(query: str) -> str | None:
    """OR-query of meaningful terms; safe to pass to to_tsquery (only [a-z0-9] tokens)."""
    terms = [w for w in _WORD.findall(query.lower()) if w not in _STOPWORDS]
    return " | ".join(dict.fromkeys(terms)) or None


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    doc_id: str
    title: str
    section_path: str
    content: str
    category: str
    tiers: list[str]
    similarity: float
    rrf_score: float

    def citation(self) -> str:
        return f"{self.doc_id}#{self.section_path.split(' > ')[-1]}"


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    tier_filter: str | None
    chunks: list[RetrievedChunk]
    top_similarity: float
    band: ConfidenceBand


def confidence_band(top_similarity: float, settings: Settings) -> ConfidenceBand:
    if top_similarity >= settings.rag_high_confidence:
        return "high"
    if top_similarity >= settings.rag_low_confidence:
        return "medium"
    return "low"


class KnowledgeStore:
    def __init__(self, pool: asyncpg.Pool, llm: GeminiClient, settings: Settings) -> None:
        self.pool = pool
        self.llm = llm
        self.settings = settings
        self._query_cache: OrderedDict[str, list[float]] = OrderedDict()

    # ------------------------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------------------------
    async def ingest_directory(self, directory: Path, *, force: bool = False) -> dict[str, int]:
        docs = load_corpus(directory)
        existing = {
            r["doc_id"]: r["checksum"] for r in await self.pool.fetch("SELECT doc_id, checksum FROM kb_documents")
        }
        stats = {"documents": len(docs), "updated": 0, "skipped": 0, "removed": 0, "chunks": 0}
        for doc in docs:
            if not force and existing.get(doc.doc_id) == doc.checksum:
                stats["skipped"] += 1
                continue
            await self._ingest_document(doc)
            stats["updated"] += 1
            stats["chunks"] += len(doc.chunks)
        stale = set(existing) - {d.doc_id for d in docs}
        if stale:
            await self.pool.execute("DELETE FROM kb_documents WHERE doc_id = ANY($1::text[])", list(stale))
            stats["removed"] = len(stale)
        log.info("kb.ingested", **stats)
        return stats

    async def _ingest_document(self, doc: PolicyDocument) -> None:
        vectors, _ = await self.llm.embed([c.embedding_text for c in doc.chunks], task_type="RETRIEVAL_DOCUMENT")
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO kb_documents (doc_id, title, category, tiers, checksum, updated_at)
                VALUES ($1, $2, $3, $4, $5, now())
                ON CONFLICT (doc_id) DO UPDATE
                SET title = EXCLUDED.title, category = EXCLUDED.category, tiers = EXCLUDED.tiers,
                    checksum = EXCLUDED.checksum, updated_at = now()
                """,
                doc.doc_id,
                doc.title,
                doc.category,
                doc.tiers,
                doc.checksum,
            )
            await conn.execute("DELETE FROM kb_chunks WHERE doc_id = $1", doc.doc_id)
            await conn.executemany(
                """
                INSERT INTO kb_chunks (doc_id, chunk_index, section_path, content, tiers, category, token_count, embedding)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)
                """,
                [
                    (
                        c.doc_id,
                        c.chunk_index,
                        c.section_path,
                        c.content,
                        c.tiers,
                        c.category,
                        c.token_count,
                        to_pgvector(v),
                    )
                    for c, v in zip(doc.chunks, vectors, strict=True)
                ],
            )

    async def chunk_count(self) -> int:
        return int(await self.pool.fetchval("SELECT count(*) FROM kb_chunks"))

    # ------------------------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------------------------
    async def _embed_query(self, query: str, trace: TurnTrace | None) -> list[float]:
        key = query.strip().lower()
        if key in self._query_cache:
            self._query_cache.move_to_end(key)
            return self._query_cache[key]
        vectors, tokens = await self.llm.embed([query], task_type="RETRIEVAL_QUERY")
        if trace:
            trace.record_usage(self.settings.embedding_model, input_tokens=tokens)
        self._query_cache[key] = vectors[0]
        if len(self._query_cache) > 512:
            self._query_cache.popitem(last=False)
        return vectors[0]

    async def search(
        self,
        query: str,
        *,
        tier: str | None,
        trace: TurnTrace | None = None,
        top_k: int | None = None,
    ) -> RetrievalResult:
        s = self.settings
        top_k = top_k or s.rag_top_k
        vec = to_pgvector(await self._embed_query(query, trace))
        tiers = [tier] if tier else ["free", "starter", "pro", "enterprise"]
        rows = await self.pool.fetch(
            """
            WITH dense AS (
                SELECT id, row_number() OVER (ORDER BY embedding <=> $1::vector) AS rnk
                FROM kb_chunks WHERE tiers && $2::text[]
                ORDER BY embedding <=> $1::vector LIMIT $3
            ),
            sparse AS (
                SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS rnk
                FROM kb_chunks, to_tsquery('english', coalesce($4, '')) q
                WHERE $4 IS NOT NULL AND tsv @@ q AND tiers && $2::text[]
                ORDER BY ts_rank_cd(tsv, q) DESC LIMIT $3
            ),
            fused AS (
                SELECT coalesce(d.id, s.id) AS id,
                       coalesce(1.0 / ($5 + d.rnk), 0) + coalesce(1.0 / ($5 + s.rnk), 0) AS rrf
                FROM dense d FULL OUTER JOIN sparse s ON d.id = s.id
            )
            SELECT c.id, c.doc_id, k.title, c.section_path, c.content, c.category, c.tiers,
                   1 - (c.embedding <=> $1::vector) AS similarity, f.rrf
            FROM fused f
            JOIN kb_chunks c ON c.id = f.id
            JOIN kb_documents k ON k.doc_id = c.doc_id
            ORDER BY f.rrf DESC
            LIMIT $6
            """,
            vec,
            tiers,
            s.rag_candidate_k,
            keyword_tsquery(query),
            _RRF_K,
            top_k,
        )
        chunks = [
            RetrievedChunk(
                chunk_id=r["id"],
                doc_id=r["doc_id"],
                title=r["title"],
                section_path=r["section_path"],
                content=r["content"],
                category=r["category"],
                tiers=list(r["tiers"]),
                similarity=float(r["similarity"]),
                rrf_score=float(r["rrf"]),
            )
            for r in rows
        ]
        top = max((c.similarity for c in chunks), default=0.0)
        return RetrievalResult(
            query=query, tier_filter=tier, chunks=chunks, top_similarity=top, band=confidence_band(top, s)
        )
