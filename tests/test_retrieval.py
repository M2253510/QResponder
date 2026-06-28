"""Hybrid retrieval tests (B1) — offline via stub embedder/reranker.

The stub embedder returns a constant vector so the DENSE signal is flat (it
"misses"); this proves the exact-term/acronym match is found via BM25 and
survives RRF + rerank. Tag-scoping bounds retrieval. No network, no model
downloads.
"""

from pathlib import Path

from qresponder.config import Config
from qresponder.core.orchestrate import orchestrate
from qresponder.kb.base import KBChunk, lexical_similarity
from qresponder.kb.library import AnswerLibrary
from qresponder.kb.retrieval import RetrievalKB, chunk_kb_dir
from qresponder.llm.mock import MockProvider
from qresponder.models import AnswerType, Confidence, Question, Status

FIX = Path(__file__).parent / "fixtures"


class StubEmbedder:
    """Flat dense signal — every text maps to the same vector."""

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class StubReranker:
    """Deterministic cross-encoder stand-in: rank by lexical overlap."""

    def rerank(self, query, docs):
        scored = [(i, lexical_similarity(query, d)) for i, d in enumerate(docs)]
        return sorted(scored, key=lambda x: x[1], reverse=True)


def _kb():
    chunks = [
        KBChunk(source="certs.md", text="We maintain ISO 27001 certification, audited annually by an accredited body.", tags=["soc2"], tier=2),
        KBChunk(source="offices.md", text="Our offices are located in Berlin and Tokyo.", tags=["soc2"], tier=2),
        KBChunk(source="training.md", text="Employees complete annual security awareness training.", tags=["gdpr"], tier=2),
    ]
    return RetrievalKB(chunks, embedder=StubEmbedder(), reranker=StubReranker(),
                       top_n=20, top_k=5, rrf_k=60)


def test_exact_term_retrieved_via_bm25_when_dense_is_flat():
    kb = _kb()
    hits = kb.retrieve("ISO 27001 certification", scope_tags=["soc2"])
    assert hits, "expected retrieved chunks"
    top_chunk, top_score = hits[0]
    # BM25 surfaces the acronym match even though dense gives no signal.
    assert "ISO 27001" in top_chunk.text
    # Tag-scoping: the gdpr-only chunk is never returned under a soc2 scope.
    assert all("soc2" in c.tags for c, _ in hits)
    assert all(c.source != "training.md" for c, _ in hits)


def test_chunking_respects_size_and_structure():
    chunks = chunk_kb_dir(FIX / "kb")
    assert chunks
    # Chunks stay under the reranker-safe word cap.
    assert all(len(c.text.split()) <= 480 for c in chunks)
    # Tags propagate from the source file.
    assert any("encryption" in c.tags for c in chunks)


def test_retrieval_mode_generated_can_reach_high(monkeypatch):
    """B1+B2: in retrieval mode a generated answer with a strong rerank score
    and a passed faithfulness check reaches HIGH confidence."""
    chunks = [
        KBChunk(source="enc.md", text="All customer data at rest is encrypted using AES-256 with keys in KMS.", tags=["soc2"], tier=2),
        KBChunk(source="net.md", text="Network traffic is monitored continuously.", tags=["soc2"], tier=2),
    ]
    kb = RetrievalKB(chunks, embedder=StubEmbedder(), reranker=StubReranker(),
                     top_n=20, top_k=5, rrf_k=60)
    cfg = Config(llm_provider="mock", kb_mode="retrieval", verify_faithfulness=True,
                 strong_rerank_score=0.0)
    questions = [Question(id="q1", text="Do you encrypt data at rest with AES-256?", answer_type=AnswerType.YES_NO)]

    results = orchestrate(questions, MockProvider(), AnswerLibrary([]), kb, cfg, scope_tags=["soc2"])
    r = results[0]
    assert r.status == Status.ANSWERED
    assert r.source_tier == 2
    assert r.citations and all(c.faithful is True for c in r.citations)
    assert r.confidence == Confidence.HIGH
