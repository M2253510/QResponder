"""Faithfulness verification + confidence rule tests (B2). No network."""

from qresponder.config import Config
from qresponder.core.confidence import decide_confidence
from qresponder.core.faithfulness import verify_results
from qresponder.llm.mock import MockProvider
from qresponder.models import (
    AnswerResult,
    AnswerType,
    Citation,
    Confidence,
    ReviewReason,
    Status,
)


def _generated_answered(qid="q1", answer="We are ISO 27001 certified."):
    return AnswerResult(
        question_id=qid,
        question_text="Are you certified?",
        answer=answer,
        answer_type=AnswerType.TEXT,
        citations=[Citation(source="kb", snippet="ISO 27001 certification audited annually")],
        confidence=Confidence.MEDIUM,
        status=Status.ANSWERED,
        source_tier=2,
    )


def test_faithfulness_flags_unentailed():
    r = _generated_answered()
    provider = MockProvider(responses=['[{"id":"q1","faithful":false,"unsupported_claims":["no audit date"]}]'])
    verify_results(provider, [r], Config(verify_faithfulness=True))
    assert r.status == Status.NEEDS_REVIEW
    assert r.review_reason == ReviewReason.FAITHFULNESS_FAIL
    assert r.confidence == Confidence.LOW
    assert r.citations[0].faithful is False
    assert r.missing_info and "faithfulness" in r.missing_info.lower()


def test_faithfulness_keeps_entailed():
    r = _generated_answered()
    provider = MockProvider(responses=['[{"id":"q1","faithful":true,"unsupported_claims":[]}]'])
    verify_results(provider, [r], Config(verify_faithfulness=True))
    assert r.status == Status.ANSWERED
    assert r.citations[0].faithful is True


def test_faithfulness_exempts_tier1():
    """F5: Tier-1 results are never sent to the judge."""
    tier1 = AnswerResult(
        question_id="q1",
        question_text="Encrypt at rest?",
        answer="Yes, AES-256.",
        answer_type=AnswerType.YES_NO,
        citations=[Citation(source="Answer Library", snippet="Yes, AES-256.", faithful=True)],
        confidence=Confidence.HIGH,
        status=Status.ANSWERED,
        source_tier=1,
    )
    provider = MockProvider()  # would record any call
    verify_results(provider, [tier1], Config(verify_faithfulness=True))
    assert provider.calls == []  # judge never invoked
    assert tier1.status == Status.ANSWERED
    assert tier1.confidence == Confidence.HIGH


def test_faithfulness_disabled_skips_check():
    r = _generated_answered()
    provider = MockProvider()
    verify_results(provider, [r], Config(verify_faithfulness=False))
    assert provider.calls == []
    assert r.status == Status.ANSWERED
    assert r.citations[0].faithful is None  # left unverified


def test_confidence_rule():
    # Tier-1 -> HIGH regardless.
    assert decide_confidence(source_tier=1, status=Status.ANSWERED, faithful=False, retrieval_score=None) == Confidence.HIGH
    # Generated + faithful + strong retrieval -> HIGH.
    assert decide_confidence(source_tier=2, status=Status.ANSWERED, faithful=True, retrieval_score=0.8) == Confidence.HIGH
    # Generated + faithful but no/weak retrieval signal -> MEDIUM.
    assert decide_confidence(source_tier=2, status=Status.ANSWERED, faithful=True, retrieval_score=None) == Confidence.MEDIUM
    # Generated + strong retrieval but not faithful -> MEDIUM (answered) — but in
    # practice an unfaithful result is already NEEDS_REVIEW -> LOW:
    assert decide_confidence(source_tier=2, status=Status.NEEDS_REVIEW, faithful=False, retrieval_score=0.9) == Confidence.LOW
