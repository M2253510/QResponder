"""Hybrid retrieval KB (§7) — Phase 1, fully local.

The 2026 production-standard stack:
  * structure-aware chunking (split on headings / paragraph blocks), ~300-500
    tokens, small overlap, capped so the cross-encoder reranker sees the whole
    chunk (≤ ~512 tokens);
  * hybrid retrieval: BM25 (sparse) + dense (cosine) in parallel, fused with
    Reciprocal Rank Fusion (RRF, k=60) — BM25 nails exact control names/acronyms
    that embeddings miss; dense nails paraphrase;
  * rerank: retrieve TOP_N_RETRIEVE (20) → cross-encoder rerank → keep
    TOP_K_CONTEXT (5).

Tag-scoping (§5.3) bounds retrieval. The embedder and reranker are injectable so
this is testable offline; the defaults are local models (first run downloads
them) and the local path makes zero external network calls.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .base import KBChunk, _tokens
from .in_context import _TEXT_EXTS, _extract_tags
from .tags import in_scope

log = logging.getLogger("qresponder.retrieval")

# Word-count proxy for tokens (cheap, dependency-free). ~400 words stays well
# under the reranker's 512-token window after subword tokenization.
_CHUNK_TARGET_WORDS = 400
_CHUNK_MAX_WORDS = 480
_CHUNK_OVERLAP_WORDS = 50
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")


def _word_count(text: str) -> int:
    return len(text.split())


def _structure_blocks(text: str) -> list[str]:
    """Split into blocks on blank lines, keeping Markdown headings attached to
    the block they introduce."""
    from .in_context import _TAGS_LINE

    body = _TAGS_LINE.sub("", text)
    raw_blocks = re.split(r"\n\s*\n", body)
    return [b.strip() for b in raw_blocks if b.strip()]


def _pack_chunks(blocks: list[str]) -> list[str]:
    """Greedily pack blocks into ~target-word chunks with small overlap, never
    exceeding the max-word cap. Oversized single blocks are hard-split."""
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    def flush():
        nonlocal current, current_words
        if current:
            chunks.append("\n\n".join(current).strip())
            current = []
            current_words = 0

    for block in blocks:
        bw = _word_count(block)
        # A single oversized block: split it on word boundaries.
        if bw > _CHUNK_MAX_WORDS:
            flush()
            words = block.split()
            for i in range(0, len(words), _CHUNK_TARGET_WORDS):
                chunks.append(" ".join(words[i : i + _CHUNK_TARGET_WORDS]))
            continue
        if current_words + bw > _CHUNK_TARGET_WORDS and current:
            # Carry a small overlap tail into the next chunk for continuity.
            tail = "\n\n".join(current)
            flush()
            tail_words = tail.split()[-_CHUNK_OVERLAP_WORDS:]
            if tail_words:
                current.append(" ".join(tail_words))
                current_words = len(tail_words)
        current.append(block)
        current_words += bw
    flush()
    return [c for c in chunks if c.strip()]


def chunk_kb_dir(kb_dir: str | Path) -> list[KBChunk]:
    chunks: list[KBChunk] = []
    d = Path(kb_dir)
    if not d.exists():
        return chunks
    for fp in sorted(d.rglob("*")):
        if not fp.is_file() or fp.suffix.lower() not in _TEXT_EXTS:
            continue
        text = fp.read_text(encoding="utf-8", errors="replace")
        tags = _extract_tags(text)
        for piece in _pack_chunks(_structure_blocks(text)):
            chunks.append(KBChunk(source=fp.name, text=piece, tags=tags, tier=2))
    return chunks


def _rank_positions(scores) -> list[int]:
    """Map each index to its 0-based rank (0 = highest score)."""
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    pos = [0] * len(scores)
    for rank, idx in enumerate(order):
        pos[idx] = rank
    return pos


class RetrievalKB:
    def __init__(
        self,
        chunks: list[KBChunk] | None = None,
        embedder=None,
        reranker=None,
        *,
        top_n: int = 20,
        top_k: int = 5,
        rrf_k: int = 60,
        embedding_model: str = "all-MiniLM-L6-v2",
        reranker_model: str = "BAAI/bge-reranker-base",
    ):
        self.chunks: list[KBChunk] = chunks or []
        self._embedder = embedder
        self._reranker = reranker
        self._embedding_model = embedding_model
        self._reranker_model = reranker_model
        self.top_n = top_n
        self.top_k = top_k
        self.rrf_k = rrf_k
        self._chunk_vecs = None  # lazily computed numpy array

    @classmethod
    def load(cls, kb_dir, config, embedder=None, reranker=None) -> "RetrievalKB":
        chunks = chunk_kb_dir(kb_dir) if kb_dir else []
        log.info("Chunked KB into %d chunk(s) from %s", len(chunks), kb_dir)
        return cls(
            chunks,
            embedder=embedder,
            reranker=reranker,
            top_n=config.top_n_retrieve,
            top_k=config.top_k_context,
            rrf_k=config.rrf_k,
            embedding_model=config.embedding_model,
            reranker_model=config.reranker_model,
        )

    # Lazy local defaults so importing this module never pulls heavy deps.
    def _get_embedder(self):
        if self._embedder is None:
            from ..llm.embeddings import LocalEmbedder

            self._embedder = LocalEmbedder(self._embedding_model)
        return self._embedder

    def _get_reranker(self):
        if self._reranker is None:
            from ..llm.reranker import LocalReranker

            self._reranker = LocalReranker(self._reranker_model)
        return self._reranker

    def _ensure_vecs(self):
        if self._chunk_vecs is None:
            import numpy as np

            vecs = self._get_embedder().embed([c.text for c in self.chunks])
            self._chunk_vecs = np.asarray(vecs, dtype=float)

    def retrieve(self, query: str, scope_tags=None) -> list[tuple[KBChunk, float]]:
        """Hybrid retrieve → RRF → rerank. Returns [(chunk, rerank_score)]."""
        import numpy as np
        from rank_bm25 import BM25Okapi

        idxs = [i for i, c in enumerate(self.chunks) if in_scope(c.tags, scope_tags)]
        if not idxs:
            return []
        texts = [self.chunks[i].text for i in idxs]

        # Sparse — BM25.
        bm25 = BM25Okapi([_tokens(t) for t in texts])
        bm_scores = bm25.get_scores(_tokens(query))
        bm_pos = _rank_positions(list(bm_scores))

        # Dense — cosine over (normalized) embeddings.
        self._ensure_vecs()
        qv = np.asarray(self._get_embedder().embed([query])[0], dtype=float)
        sub = self._chunk_vecs[idxs]
        dense_scores = sub @ qv
        dense_pos = _rank_positions(list(dense_scores))

        # Fuse with Reciprocal Rank Fusion (k = rrf_k).
        fused = {}
        for local in range(len(idxs)):
            fused[local] = 1.0 / (self.rrf_k + bm_pos[local] + 1) + 1.0 / (
                self.rrf_k + dense_pos[local] + 1
            )
        top_local = sorted(fused, key=lambda i: fused[i], reverse=True)[: self.top_n]

        # Cross-encoder rerank the fused candidates; keep top_k.
        cand_texts = [texts[i] for i in top_local]
        reranked = self._get_reranker().rerank(query, cand_texts)  # [(idx_in_cand, score)]
        out: list[tuple[KBChunk, float]] = []
        for cand_idx, score in reranked[: self.top_k]:
            chunk = self.chunks[idxs[top_local[cand_idx]]]
            out.append((chunk, float(score)))
        return out

    def context_for(self, query: str, scope_tags=None) -> str:
        """Per-question reranked top-k context, each chunk prefixed [source: …]."""
        hits = self.retrieve(query, scope_tags)
        return "\n\n".join(f"[source: {c.source}] {c.text}" for c, _ in hits)

    def top_score(self, query: str, scope_tags=None) -> float | None:
        hits = self.retrieve(query, scope_tags)
        return hits[0][1] if hits else None
