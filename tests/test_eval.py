"""Eval harness tests (B3) — MockProvider, no network."""

from pathlib import Path

from qresponder.config import Config
from qresponder.eval.metrics import EvalReport
from qresponder.eval.runner import format_report, run_eval
from qresponder.kb.base import KBChunk, lexical_similarity
from qresponder.kb.retrieval import RetrievalKB
from qresponder.llm.mock import MockProvider

FIX = Path(__file__).parent / "fixtures"


def test_eval_report_has_all_metric_keys():
    cfg = Config(llm_provider="mock", kb_mode="in_context")
    report = run_eval(
        FIX / "eval.yaml",
        kb_dir=str(FIX / "kb"),
        qa_path=str(FIX / "qa.yaml"),
        config=cfg,
        provider=MockProvider(),
    )
    assert isinstance(report, EvalReport)
    # All required metric keys present.
    assert report.n_items == 3
    assert hasattr(report, "recall_at_k")
    assert report.faithfulness_rate is not None  # some items answered+faithful
    assert report.correctness is not None         # key_facts graded
    assert set(["answered", "flagged", "auto_pct", "flagged_pct", "by_reason"]) <= set(report.coverage)

    # The unsupported retention question is flagged, not answered.
    retention = next(r for r in report.items if "retention" in r.question.lower())
    assert retention.status == "needs_review"

    # format_report renders the headline metrics.
    text = format_report(report)
    assert "Recall@" in text
    assert "faithfulness" in text
    assert "correctness" in text
    assert "calibrate" in text.lower()


class _StubEmbedder:
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _StubReranker:
    def rerank(self, query, docs):
        return sorted(((i, lexical_similarity(query, d)) for i, d in enumerate(docs)),
                      key=lambda x: x[1], reverse=True)


def test_eval_recall_at_k_in_retrieval_mode():
    chunks = [
        KBChunk(source="policy_encryption.md", text="All data at rest is encrypted using AES-256.", tags=["soc2"], tier=2),
        KBChunk(source="policy_offices.md", text="Offices in Berlin.", tags=["soc2"], tier=2),
    ]
    kb = RetrievalKB(chunks, embedder=_StubEmbedder(), reranker=_StubReranker(),
                     top_n=20, top_k=5, rrf_k=60)
    cfg = Config(llm_provider="mock", kb_mode="retrieval")
    report = run_eval(
        FIX / "eval.yaml",
        kb_dir=None,
        qa_path=None,
        config=cfg,
        provider=MockProvider(),
        kb=kb,
    )
    # Recall@K is computed (not N/A) and the encryption source is retrieved.
    assert report.recall_at_k is not None
    enc = next(r for r in report.items if "encrypt" in r.question.lower())
    assert enc.recall_hit is True
