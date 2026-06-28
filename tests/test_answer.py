"""Answering + orchestration tests (mock provider, no network).

Covers the core guardrails: Tier-1 reuse, no ANSWERED without a citation,
unsupported -> NEEDS_REVIEW, attachment routing, and confidence rules.
"""

from pathlib import Path

from qresponder.config import Config
from qresponder.core.answer import answer_batch
from qresponder.core.orchestrate import orchestrate
from qresponder.kb.in_context import InContextKB
from qresponder.kb.library import AnswerLibrary, LibraryEntry
from qresponder.llm import prompts
from qresponder.llm.mock import MockProvider
from qresponder.models import AnswerType, Confidence, Question, ReviewReason, Status

FIX = Path(__file__).parent / "fixtures"


def test_answer_batch_downgrades_answered_without_citation():
    # Model claims answered but supplies no citation -> must be downgraded.
    bad = MockProvider(
        responses=['[{"question_id":"q1","answer":"Yes","answer_type":"yes_no",'
                   '"citations":[],"status":"answered","confidence":"high"}]']
    )
    res = answer_batch(bad, "ctx", [{"question_id": "q1", "question_text": "Q?", "answer_type": "yes_no"}])
    assert res[0].status == Status.NEEDS_REVIEW
    assert res[0].review_reason == ReviewReason.UNSUPPORTED


def test_answer_batch_rejects_fabricated_citation():
    """F2: an answered result citing a snippet absent from the KB context is
    downgraded (the fabricated citation is dropped, leaving no citation)."""
    context = "All data at rest is encrypted using AES-256 with keys in KMS."
    bad = MockProvider(
        responses=['[{"question_id":"q1","answer":"We hold ISO 27001 certification.",'
                   '"answer_type":"text","citations":[{"source":"kb",'
                   '"snippet":"We are ISO 27001 certified since 2019 by BSI."}],'
                   '"status":"answered","confidence":"high"}]']
    )
    res = answer_batch(bad, context, [{"question_id": "q1", "question_text": "Q?", "answer_type": "text"}])
    assert res[0].status == Status.NEEDS_REVIEW
    assert res[0].review_reason == ReviewReason.UNSUPPORTED
    assert not res[0].citations


def test_answer_batch_keeps_supported_citation():
    """A citation whose snippet IS in the context stays ANSWERED."""
    context = "All data at rest is encrypted using AES-256 with keys in KMS."
    good = MockProvider(
        responses=['[{"question_id":"q1","answer":"Yes, AES-256 at rest.",'
                   '"answer_type":"yes_no","citations":[{"source":"kb",'
                   '"snippet":"All data at rest is encrypted using AES-256"}],'
                   '"status":"answered","confidence":"high"}]']
    )
    res = answer_batch(good, context, [{"question_id": "q1", "question_text": "Q?", "answer_type": "yes_no"}])
    assert res[0].status == Status.ANSWERED
    assert res[0].citations


def test_answer_batch_parse_error_flags_review():
    bad = MockProvider(responses=["garbage", "still garbage"])
    res = answer_batch(bad, "ctx", [{"question_id": "q1", "question_text": "Q?", "answer_type": "text"}])
    assert res[0].status == Status.NEEDS_REVIEW
    assert res[0].review_reason == ReviewReason.PARSE_ERROR


def test_orchestrate_full_flow():
    cfg = Config(llm_provider="mock", kb_mode="in_context", max_kb_chars=150000, batch_size=12)
    provider = MockProvider()
    library = AnswerLibrary.load(FIX / "qa.yaml")
    kb = InContextKB.load(FIX / "kb")

    questions = [
        Question(id="q1", text="Do you encrypt data at rest?", answer_type=AnswerType.YES_NO, location_hint="S!B3"),
        Question(id="q2", text="Do you have a documented incident response plan?", answer_type=AnswerType.YES_NO),
        Question(id="q3", text="Please attach your SOC 2 report.", answer_type=AnswerType.ATTACHMENT),
        Question(id="q4", text="What is the data retention period for backups?", answer_type=AnswerType.TEXT),
    ]
    results = orchestrate(questions, provider, library, kb, cfg, scope_tags=["soc2"])
    by = {r.question_id: r for r in results}

    # q1 -> Tier-1 library reuse (HIGH, source_tier=1, cited).
    assert by["q1"].source_tier == 1
    assert by["q1"].confidence == Confidence.HIGH
    assert by["q1"].status == Status.ANSWERED
    assert by["q1"].citations

    # q2 -> not in library fixture, but supported by KB -> generated + cited.
    assert by["q2"].status == Status.ANSWERED
    assert by["q2"].citations
    assert by["q2"].confidence == Confidence.MEDIUM  # generated caps at MEDIUM

    # q3 -> attachment routed to review, never fabricated.
    assert by["q3"].status == Status.NEEDS_REVIEW
    assert by["q3"].review_reason == ReviewReason.ATTACHMENT_UNRESOLVED

    # q4 -> unsupported by KB -> NEEDS_REVIEW with missing_info, no fabrication.
    assert by["q4"].status == Status.NEEDS_REVIEW
    assert by["q4"].review_reason == ReviewReason.UNSUPPORTED
    assert by["q4"].missing_info
    assert not by["q4"].citations

    # Order preserved.
    assert [r.question_id for r in results] == ["q1", "q2", "q3", "q4"]


def test_library_band_split_blocks_meaning_flip(tmp_path):
    """F1: a meaning-flipping near-miss must NOT auto-reuse at HIGH; it is
    surfaced as a LIBRARY_CANDIDATE for human confirmation. A near-exact match
    still auto-reuses."""
    cfg = Config(llm_provider="mock")
    provider = MockProvider()
    library = AnswerLibrary(
        [
            LibraryEntry(
                question="Do you encrypt data at rest?",
                answer="Yes. Data at rest is encrypted with AES-256.",
                tags=["soc2", "encryption"],
                approved_by="security-team",
            )
        ]
    )
    kb = InContextKB.load(FIX / "kb")

    questions = [
        # Near-miss: "in transit" flips the meaning of the at-rest entry.
        Question(id="q1", text="Do you encrypt data in transit?", answer_type=AnswerType.YES_NO),
        # Near-exact: same question -> safe to auto-reuse.
        Question(id="q2", text="Do you encrypt data at rest?", answer_type=AnswerType.YES_NO),
    ]
    results = {r.question_id: r for r in orchestrate(questions, provider, library, kb, cfg)}

    # q1: NOT answered/HIGH — surfaced for confirmation.
    assert results["q1"].status == Status.NEEDS_REVIEW
    assert results["q1"].review_reason == ReviewReason.LIBRARY_CANDIDATE
    assert results["q1"].confidence == Confidence.LOW
    assert results["q1"].source_tier == 1
    assert results["q1"].answer  # proposed reuse is shown to the human
    assert results["q1"].missing_info and "confirm" in results["q1"].missing_info.lower()

    # q2: near-exact -> auto-reuse at HIGH.
    assert results["q2"].status == Status.ANSWERED
    assert results["q2"].confidence == Confidence.HIGH
    assert results["q2"].source_tier == 1


def test_every_answered_has_citation_invariant():
    cfg = Config(llm_provider="mock")
    provider = MockProvider()
    library = AnswerLibrary.load(FIX / "qa.yaml")
    kb = InContextKB.load(FIX / "kb")
    questions = [
        Question(id=f"q{i}", text=t, answer_type=AnswerType.TEXT)
        for i, t in enumerate(
            [
                "Do you encrypt data at rest?",
                "Tell us about your incident response process and plan.",
                "What is your favorite color?",
            ],
            start=1,
        )
    ]
    results = orchestrate(questions, provider, library, kb, cfg)
    for r in results:
        if r.status == Status.ANSWERED:
            assert r.citations, f"{r.question_id} answered without citation"
