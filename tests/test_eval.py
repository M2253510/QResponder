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

    # S2: score distribution + suggested threshold reported.
    assert "answered" in report.score_distribution
    assert "flagged" in report.score_distribution
    # answered items carry a grounding score in in-context mode.
    assert report.score_distribution["answered"] is not None

    # format_report renders the headline metrics + score distribution.
    text = format_report(report)
    assert "Recall@" in text
    assert "faithfulness" in text
    assert "correctness" in text
    assert "calibrate" in text.lower()
    assert "score distribution" in text.lower()
    assert "suggested" in text.lower()


def test_eval_threshold_is_config_driven():
    """S2: strong-score threshold is read from config end-to-end (changing it
    moves a borderline generated answer between MEDIUM and HIGH)."""
    chunks = [KBChunk(source="enc.md", text="Data at rest is encrypted with AES-256.", tags=["soc2"], tier=2)]
    from qresponder.kb.in_context import InContextKB
    from qresponder.core.orchestrate import orchestrate
    from qresponder.kb.library import AnswerLibrary
    from qresponder.models import AnswerType, Confidence, Question

    eid_answer = ('[{"question_id":"q1","answer":"Data at rest is encrypted with AES-256.",'
                  '"answer_type":"yes_no","citations":[{"source":"enc.md",'
                  '"snippet":"Data at rest is encrypted with AES-256."}],'
                  '"status":"answered","confidence":"low"}]')
    faith = '[{"id":"q1","faithful":true,"unsupported_claims":[]}]'
    q = [Question(id="q1", text="Encrypt at rest?", answer_type=AnswerType.YES_NO)]
    kb = InContextKB(chunks)

    # Low threshold -> strong grounding -> HIGH.
    cfg_low = Config(llm_provider="mock", kb_mode="in_context", strong_grounding_score=0.1)
    r_low = orchestrate(q, MockProvider(responses=[eid_answer, faith]), AnswerLibrary([]), kb, cfg_low)[0]
    assert r_low.confidence == Confidence.HIGH

    # Impossibly high threshold -> never strong -> MEDIUM.
    cfg_high = Config(llm_provider="mock", kb_mode="in_context", strong_grounding_score=1.1)
    r_high = orchestrate(q, MockProvider(responses=[eid_answer, faith]), AnswerLibrary([]), kb, cfg_high)[0]
    assert r_high.confidence == Confidence.MEDIUM


def test_shipped_golden_eval_runs_end_to_end():
    """D2: the in-repo golden eval.yaml runs against the example KB + Library and
    produces a report with all metric keys out of the box."""
    root = Path(__file__).parent.parent
    cfg = Config(llm_provider="mock", kb_mode="in_context")
    report = run_eval(
        root / "eval.yaml",
        kb_dir=str(root / "tests" / "fixtures" / "kb"),
        qa_path=str(root / "qa.example.yaml"),
        config=cfg,
        provider=MockProvider(),
    )
    assert report.n_items == 20
    # All headline metric keys present and computed.
    assert report.faithfulness_rate is not None
    assert report.correctness is not None
    assert report.coverage["answered"] + report.coverage["flagged"] == 20
    assert report.coverage["flagged"] >= 1  # the intentionally-unsupported items
    assert report.score_distribution.get("answered") is not None
    # Part A: RAGAS-aligned metrics, calibration, abstention all present.
    assert report.answer_relevancy is not None
    assert report.context_recall is not None
    assert report.mrr is None or isinstance(report.mrr, float)  # n/a in in-context
    assert set(report.calibration) == {"high", "medium", "low"}
    assert report.abstention["rate"] >= 0
    text = format_report(report)
    for key in ("Recall@", "faithfulness", "correctness", "RAGAS", "abstention", "calibration", "score distribution"):
        assert key in text


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
    # Part A: MRR computed in retrieval mode; rank recorded.
    assert report.mrr is not None and 0 < report.mrr <= 1
    assert enc.recall_rank == 1


def test_calibration_high_beats_low():
    """Part A: HIGH-confidence answers measure at least as correct as MEDIUM —
    the calibration table proves 'HIGH means HIGH'."""
    root = Path(__file__).parent.parent
    cfg = Config(llm_provider="mock", kb_mode="in_context")
    report = run_eval(root / "eval.yaml", kb_dir=str(root / "tests" / "fixtures" / "kb"),
                      qa_path=str(root / "qa.example.yaml"), config=cfg, provider=MockProvider())
    hi = report.calibration["high"]["correctness"]
    med = report.calibration["medium"]["correctness"]
    assert hi is not None and med is not None
    assert hi >= med  # higher predicted confidence -> at least as correct


def test_abstention_is_first_class():
    root = Path(__file__).parent.parent
    cfg = Config(llm_provider="mock", kb_mode="in_context")
    report = run_eval(root / "eval.yaml", kb_dir=str(root / "tests" / "fixtures" / "kb"),
                      qa_path=str(root / "qa.example.yaml"), config=cfg, provider=MockProvider())
    assert report.abstention["rate"] > 0
    assert "unsupported" in report.abstention["by_reason"]
