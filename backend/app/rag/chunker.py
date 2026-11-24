"""Structure-aware Markdown chunker for support policies.

Design choices (see README "Chunking"):
* Chunks never cross a heading boundary: a policy section ("Refund limits by tier") is the unit a
  support answer cites, so mixing two sections in one chunk dilutes similarity scores.
* Tables, lists and code blocks are atomic blocks - splitting a refund-limit table mid-row is the
  classic way RAG systems hallucinate numbers.
* Long sections are packed block-by-block up to ``target_tokens`` with a one-block overlap so a
  sentence that straddles a boundary is retrievable from either side.
* Every chunk carries a breadcrumb ("Refund Policy > Refund limits by tier") which is prepended to
  the embedded text. Short sections stay precise while still carrying document context.
* Document front-matter (category, tiers) is copied onto each chunk so retrieval can filter by the
  verified customer's subscription tier.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

ALL_TIERS = ["free", "starter", "pro", "enterprise"]


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token for English). Good enough for chunk sizing."""
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    chunk_index: int
    title: str
    section_path: str
    content: str
    category: str
    tiers: list[str]

    @property
    def token_count(self) -> int:
        return estimate_tokens(self.content)

    @property
    def embedding_text(self) -> str:
        return f"{self.section_path}\n\n{self.content}"


@dataclass
class PolicyDocument:
    doc_id: str
    title: str
    category: str
    tiers: list[str]
    body: str
    checksum: str
    chunks: list[Chunk] = field(default_factory=list)


@dataclass
class _Section:
    path: list[str]
    lines: list[str] = field(default_factory=list)


def _split_blocks(lines: list[str]) -> list[str]:
    """Group lines into atomic blocks: paragraph, list, table or fenced code."""
    blocks: list[str] = []
    current: list[str] = []
    kind: str | None = None
    in_code = False

    def flush() -> None:
        nonlocal current, kind
        text = "\n".join(current).strip()
        if text:
            blocks.append(text)
        current, kind = [], None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                current.append(line)
                in_code = False
                flush()
                continue
            flush()
            in_code, kind = True, "code"
            current.append(line)
            continue
        if in_code:
            current.append(line)
            continue
        if not stripped:
            # Blank lines end paragraphs but not lists (loose lists are common in policies).
            if kind != "list":
                flush()
            continue
        line_kind = "table" if stripped.startswith("|") else "list" if _LIST_ITEM.match(line) else "para"
        if kind == "list" and line_kind == "para" and line.startswith((" ", "\t")):
            line_kind = "list"  # continuation of a list item
        if kind is not None and line_kind != kind:
            flush()
        kind = line_kind
        current.append(line)
    flush()
    return blocks


def _split_oversized(block: str, max_tokens: int) -> list[str]:
    """Split a single oversized block. Lists/tables split on rows, prose on sentences."""
    if estimate_tokens(block) <= max_tokens:
        return [block]
    is_table = block.lstrip().startswith("|")
    units = block.split("\n") if is_table or _LIST_ITEM.match(block) else _SENTENCE_SPLIT.split(block)
    header = units[:2] if is_table else []  # repeat table header on every piece
    body = units[2:] if is_table else units
    joiner = "\n" if is_table or "\n" in block else " "
    pieces: list[str] = []
    buf: list[str] = []
    for unit in body:
        candidate = joiner.join(header + buf + [unit])
        if buf and estimate_tokens(candidate) > max_tokens:
            pieces.append(joiner.join(header + buf))
            buf = []
        buf.append(unit)
    if buf:
        pieces.append(joiner.join(header + buf))
    return pieces


def _pack(blocks: list[str], target_tokens: int, overlap_max_tokens: int) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    for block in blocks:
        for piece in _split_oversized(block, target_tokens):
            candidate = "\n\n".join(buf + [piece])
            if buf and estimate_tokens(candidate) > target_tokens:
                chunks.append("\n\n".join(buf))
                tail = buf[-1]
                # Carry the last block forward as overlap if it is small enough.
                buf = [tail] if estimate_tokens(tail) <= overlap_max_tokens else []
            buf.append(piece)
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def chunk_markdown(
    *,
    doc_id: str,
    title: str,
    body: str,
    category: str,
    tiers: list[str],
    target_tokens: int = 320,
    overlap_max_tokens: int = 60,
) -> list[Chunk]:
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []
    current = _Section(path=[title])
    for line in body.splitlines():
        m = _HEADING.match(line)
        if m:
            sections.append(current)
            level, heading = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            path = [h for _, h in stack]
            if path and path[0] == title:  # H1 duplicates the doc title
                path = path[1:]
            current = _Section(path=[title, *path])
            continue
        current.lines.append(line)
    sections.append(current)

    chunks: list[Chunk] = []
    for section in sections:
        blocks = _split_blocks(section.lines)
        if not blocks:
            continue
        for text in _pack(blocks, target_tokens, overlap_max_tokens):
            chunks.append(
                Chunk(
                    doc_id=doc_id,
                    chunk_index=len(chunks),
                    title=title,
                    section_path=" > ".join(section.path),
                    content=text,
                    category=category,
                    tiers=tiers,
                )
            )
    return chunks


def load_policy(path: Path, **chunk_kwargs: int) -> PolicyDocument:
    raw = path.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    meta = post.metadata
    raw_tiers = meta.get("tiers", ["all"])
    tiers = [str(t).lower() for t in (raw_tiers if isinstance(raw_tiers, list) else [raw_tiers])]
    if "all" in tiers:
        tiers = list(ALL_TIERS)
    unknown = set(tiers) - set(ALL_TIERS)
    if unknown:
        raise ValueError(f"{path.name}: unknown tiers {sorted(unknown)}")
    doc = PolicyDocument(
        doc_id=str(meta.get("doc_id") or path.stem),
        title=str(meta.get("title") or path.stem),
        category=str(meta.get("category", "general")),
        tiers=tiers,
        body=post.content,
        checksum=hashlib.sha256(raw.encode()).hexdigest(),
    )
    doc.chunks = chunk_markdown(
        doc_id=doc.doc_id, title=doc.title, body=doc.body, category=doc.category, tiers=doc.tiers, **chunk_kwargs
    )
    return doc


def load_corpus(directory: Path) -> list[PolicyDocument]:
    return [load_policy(p) for p in sorted(directory.glob("*.md"))]
