"""Audit / evidence pack tests (Part B). Offline."""

from pathlib import Path

from qresponder.config import Config
from qresponder.core.orchestrate import orchestrate
from qresponder.kb.base import KBChunk, lexical_similarity
from qresponder.kb.library import AnswerLibrary
from qresponder.kb.retrieval import RetrievalKB
from qresponder.llm.mock import MockProvider
from qresponder.models import AnswerType, Question, Status
from qresponder.output.audit import build_audit_md, write_audit


class _StubEmbedder:
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _StubReranker:
    def rerank(self, query, docs):
        return sorted(((i, lexical_similarity(query, d)) for i, d in enumerate(docs)),
                      key=lambda x: x[1], reverse=True)


def _retrieval_kb():
    chunks = [
        KBChunk(source="enc.md", text="All customer data at rest is encrypted using AES-256.", tags=["soc2"], tier=2),
        KBChunk(source="net.md", text="Network traffic is monitored continuously.", tags=["soc2"], tier=2),
    ]
    return RetrievalKB(chunks, embedder=_StubEmbedder(), reranker=_StubReranker(), top_n=20, top_k=5, rrf_k=60)


def test_generated_answer_has_populated_audit_trail():
    cfg = Config(llm_provider="mock", kb_mode="retrieval", verify_faithfulness=True)
    q = [Question(id="q1", text="Do you encrypt data at rest with AES-256?", answer_type=AnswerType.YES_NO)]
    r = orchestrate(q, MockProvider(), AnswerLibrary([]), _retrieval_kb(), cfg, scope_tags=["soc2"])[0]
    assert r.status == Status.ANSWERED
    a = r.audit
    assert a is not None
    assert a.retrieved and a.retrieved[0].source == "enc.md" and a.retrieved[0].score is not None
    assert a.cited and a.cited[0].snippet
    assert a.faithfulness.get("passed") is True
    assert "HIGH" in a.confidence_rationale or "MEDIUM" in a.confidence_rationale
    assert a.human_action.type == "none"  # not yet reviewed


def test_tier1_result_has_audit():
    lib = AnswerLibrary([])
    from qresponder.kb.library import LibraryEntry
    lib.entries.append(LibraryEntry(question="Do you encrypt data at rest?", answer="Yes, AES-256.", tags=["soc2"]))
    cfg = Config(llm_provider="mock", kb_mode="retrieval")
    q = [Question(id="q1", text="Do you encrypt data at rest?", answer_type=AnswerType.YES_NO)]
    r = orchestrate(q, MockProvider(), lib, _retrieval_kb(), cfg, scope_tags=["soc2"])[0]
    assert r.source_tier == 1 and r.audit is not None
    assert "Tier-1" in r.audit.confidence_rationale


def test_write_audit_produces_json_and_md(tmp_path):
    cfg = Config(llm_provider="mock", kb_mode="retrieval")
    q = [Question(id="q1", text="Do you encrypt data at rest with AES-256?", answer_type=AnswerType.YES_NO)]
    from qresponder.models import QuestionnaireResult
    result = QuestionnaireResult(source_file="q.xlsx",
                                 results=orchestrate(q, MockProvider(), AnswerLibrary([]), _retrieval_kb(), cfg, scope_tags=["soc2"]))
    paths = write_audit(result, tmp_path)
    assert Path(paths["json"]).exists() and Path(paths["md"]).exists()
    md = build_audit_md(result)
    assert "Evidence pack" in md
    assert "Retrieved (considered)" in md
    assert "Cited" in md
    assert "Faithfulness" in md
    assert "Confidence rationale" in md
