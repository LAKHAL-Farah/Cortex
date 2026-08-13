"""Loads and chunks the docs/knowledge/ corpus for embedding.

docs/knowledge/ is the RIF SAS OpenStack infra knowledge base: top-level topic
files (topology.md, network.md, security-access.md, admin-runbook.md, ...) plus a
service-detail/ subfolder with one file per OpenStack service (nova.md,
neutron.md, glance.md, keystone.md, cinder.md). This module is deliberately
dumb about *what* the docs say -- it only knows how to walk the directory and
split each file into retrieval-sized chunks. Everything else (embedding,
storage) lives in sibling modules so this stays independently testable without
a network connection.
"""
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

# Fixed namespace so chunk IDs are stable across runs -- re-ingesting the same
# source file/heading always upserts the same Qdrant point instead of
# accumulating duplicates every time the on-demand pipeline runs.
_CHUNK_ID_NAMESPACE = uuid.UUID("9b1a5b3e-3b7a-4b8e-9c2b-3f7b6e2a7c11")

# Markdown headings (## / ###) are the natural chunk boundary for this corpus:
# every knowledge file is a series of short, topic-scoped sections rather than
# continuous prose, so splitting on headings keeps each chunk semantically
# coherent without needing a token-aware splitter.
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)

# Chunks larger than this are further split on paragraph boundaries so a
# single embedding call/vector doesn't have to represent an oversized
# section (e.g. a long command reference or table-heavy section).
MAX_CHUNK_CHARS = 2000

# Top-level files each get their own category instead of a shared "general"
# bucket. Before this, every non-service-detail file (topology, network,
# security-access, admin-runbook, ...) was tagged "general", so the
# `category` filter already exposed by /api/v1/knowledge/search and
# qdrant_store.search() could only ever mean "service-detail vs everything
# else" -- not useful for e.g. a security-focused agent that wants to search
# security-access.md + admin-runbook.md without pulling in glossary.md hits.
# Any top-level file not listed here still falls back to "general" (see
# _category_for below), so adding a new file never breaks ingestion.
_TOP_LEVEL_CATEGORIES = {
    "README.md": "overview",
    "topology.md": "topology",
    "network.md": "network",
    "service-catalog.md": "service-catalog",
    "resource-mgmt.md": "resource-mgmt",
    "security-access.md": "security-access",
    "admin-runbook.md": "admin-runbook",
    "flow-processes.md": "flow-processes",
    "glossary.md": "glossary",
}


@dataclass
class KnowledgeChunk:
    id: str
    text: str
    source_path: str  # path relative to docs/knowledge/, e.g. "service-detail/nova.md"
    doc_title: str  # first H1 in the source file, falls back to filename
    heading: str | None  # nearest heading above this chunk, if any
    category: str  # "service-detail" for files under service-detail/, else "general"
    chunk_index: int


def _chunk_id(source_path: str, chunk_index: int) -> str:
    digest = hashlib.sha1(f"{source_path}::{chunk_index}".encode("utf-8")).hexdigest()
    return str(uuid.uuid5(_CHUNK_ID_NAMESPACE, digest))


def _split_oversized(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts, buf = [], []
    length = 0
    for para in text.split("\n\n"):
        if length + len(para) > max_chars and buf:
            parts.append("\n\n".join(buf))
            buf, length = [], 0
        buf.append(para)
        length += len(para) + 2
    if buf:
        parts.append("\n\n".join(buf))
    return parts


def _split_by_heading(text: str) -> list[tuple[str | None, str]]:
    """Returns [(heading_or_None, section_text), ...] for one file's content."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [(None, text.strip())] if text.strip() else []

    sections = []
    # Anything before the first heading (rare, but keep it rather than drop it)
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append((None, preamble))

    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        section_text = f"{heading}\n{body}" if body else heading
        sections.append((heading, section_text))
    return sections


def chunk_markdown(text: str, source_path: str, doc_title: str, category: str) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    idx = 0
    for heading, section_text in _split_by_heading(text):
        for piece in _split_oversized(section_text, MAX_CHUNK_CHARS):
            piece = piece.strip()
            if not piece:
                continue
            chunks.append(
                KnowledgeChunk(
                    id=_chunk_id(source_path, idx),
                    text=piece,
                    source_path=source_path,
                    doc_title=doc_title,
                    heading=heading,
                    category=category,
                    chunk_index=idx,
                )
            )
            idx += 1
    return chunks


def _extract_title(text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.*)$", text, re.MULTILINE)
    return m.group(1).strip() if m else fallback


def iter_markdown_files(knowledge_dir: Path):
    """Yields (absolute_path, relative_path) for every .md file under knowledge_dir,
    sorted for deterministic ingestion order."""
    for path in sorted(knowledge_dir.rglob("*.md")):
        if path.is_file():
            yield path, path.relative_to(knowledge_dir)


def _category_for(rel_path: Path) -> str:
    """A file under a `service-detail/` subdirectory (anywhere in the tree) is
    tagged category="service-detail" -- this lets the search/ingest API filter
    to "just the per-service docs" without hardcoding the five current service
    names, so a sixth service file added later picks up the same tag for free.
    Every other known top-level file gets its own category (see
    _TOP_LEVEL_CATEGORIES); an unrecognized top-level file falls back to
    "general" rather than failing ingestion.
    """
    if "service-detail" in rel_path.parts[:-1]:
        return "service-detail"
    return _TOP_LEVEL_CATEGORIES.get(rel_path.name, "general")


def load_knowledge_chunks(knowledge_dir: str | os.PathLike) -> list[KnowledgeChunk]:
    """Walks knowledge_dir and returns every chunk from every .md file in it."""
    knowledge_dir = Path(knowledge_dir)
    all_chunks: list[KnowledgeChunk] = []
    for abs_path, rel_path in iter_markdown_files(knowledge_dir):
        text = abs_path.read_text(encoding="utf-8")
        rel_str = rel_path.as_posix()
        category = _category_for(rel_path)
        title = _extract_title(text, fallback=rel_path.stem)
        all_chunks.extend(chunk_markdown(text, rel_str, title, category))
    return all_chunks
