"""Answer-type enforcement tests (Phase 7 Part D). Includes the grounding negative case."""

from qresponder.config import Config
from qresponder.core.orchestrate import orchestrate
from qresponder.core.typeshape import coerce_to_options, shape_to_type
from qresponder.kb.base import KBChunk
from qresponder.kb.in_context import InContextKB
from qresponder.kb.library import AnswerLibrary
from qresponder.llm.mock import MockProvider
from qresponder.models import (
    AnswerResult,
    AnswerType,
    Confidence,
    Question,
    Status,
)


def test_coerce_to_options():
    assert coerce_to_options("Yes, we support SSO.", ["Yes", "No"]) == "Yes"
    assert coerce_to_options("No.", ["Yes", "No"]) == "No"
    assert coerce_to_options("Partially compliant", ["Compliant", "Non-compliant", "Partial"]) == "Partial"
    assert coerce_to_options("totally unrelated", ["Yes", "No"]) is None  # no forced map
    # Word-boundary: a short option must not match inside a longer word.
    assert coerce_to_options("We do not know the retention period.", ["Yes", "No"]) is None
    assert coerce_to_options("Currently unknown, needs review.", ["Yes", "No"]) is None
    assert coerce_to_options("The answer is no, not supported.", ["Yes", "No"]) == "No"  # standalone token


def _answered(answer, atype):
    return AnswerResult(question_id="q1", question_text="Q?", answer=answer, answer_type=atype,
                        confidence=Confidence.MEDIUM, status=Status.ANSWERED,
                        citations=[], source_tier=2)


def test_select_answer_shaped_to_allowed_option():
    r = _answered("Yes, single sign-on is supported.", AnswerType.MULTI_SELECT)
    shape_to_type(r, ["Yes", "No"])
    assert r.answer == "Yes"
    assert r.status == Status.ANSWERED


def test_unmappable_answer_left_grounded_not_forced():
    """NEGATIVE CASE: an answer that doesn't match any option is NOT forced into
    one — the grounded text is kept (and could be reviewed), never fabricated."""
    r = _answered("We use a passwordless approach described in our policy.", AnswerType.MULTI_SELECT)
    shape_to_type(r, ["Yes", "No"])
    assert r.answer.startswith("We use a passwordless")  # unchanged, not 'Yes'/'No'


def test_shape_never_touches_abstention():
    """NEGATIVE CASE: type enforcement can't turn an abstention into an answer."""
    flagged = AnswerResult(question_id="q1", question_text="Q?", answer="", answer_type=AnswerType.YES_NO,
                           confidence=Confidence.LOW, status=Status.NEEDS_REVIEW)
    shape_to_type(flagged, ["Yes", "No"])
    assert flagged.status == Status.NEEDS_REVIEW
    assert flagged.answer == ""


def _kb():
    return InContextKB([KBChunk(source="enc.md", text="Data at rest is encrypted with AES-256.", tags=["soc2"], tier=2)])


def test_yes_no_supported_grounded_unsupported_abstains():
    cfg = Config(llm_provider="mock", kb_mode="in_context")
    # Supported yes/no -> grounded + cited (not forced).
    supported = orchestrate([Question(id="q1", text="Do you encrypt data at rest?", answer_type=AnswerType.YES_NO)],
                            MockProvider(), AnswerLibrary([]), _kb(), cfg, scope_tags=["soc2"])[0]
    assert supported.status == Status.ANSWERED and supported.citations
    # Unsupported yes/no -> abstains, never guesses 'Yes'.
    unsupported = orchestrate([Question(id="q2", text="Do you offer a bug bounty program?", answer_type=AnswerType.YES_NO)],
                              MockProvider(), AnswerLibrary([]), _kb(), cfg, scope_tags=["soc2"])[0]
    assert unsupported.status == Status.NEEDS_REVIEW
    assert "yes" not in (unsupported.answer or "").lower()
