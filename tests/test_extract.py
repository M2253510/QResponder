"""Extraction tests (call #1) — mock provider, no network."""

from pathlib import Path

from qresponder.core.extract import extract_questions
from qresponder.core.parsing import parse_json_array
from qresponder.ingest.base import load_document
from qresponder.llm.mock import MockProvider
from qresponder.models import AnswerType

FIX = Path(__file__).parent / "fixtures"


def test_parse_json_array_handles_fences_and_prose():
    assert parse_json_array('```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert parse_json_array('Sure! [{"a":1}] done') == [{"a": 1}]
    assert parse_json_array('{"a":1}') == [{"a": 1}]


def test_extract_from_xlsx_fixture():
    doc = load_document(FIX / "sample.xlsx")
    questions = extract_questions(doc, MockProvider())
    texts = [q.text for q in questions]
    assert any("encrypt data at rest" in t.lower() for t in texts)
    assert any("multi-factor" in t.lower() for t in texts)

    # The attachment request is typed as an attachment.
    attach = [q for q in questions if q.answer_type == AnswerType.ATTACHMENT]
    assert attach and "soc 2" in attach[0].text.lower()

    # Write-back anchors and yes/no typing survive.
    enc = next(q for q in questions if "encrypt" in q.text.lower())
    assert enc.location_hint and enc.location_hint.startswith("Security!")
    assert enc.answer_type == AnswerType.YES_NO


def test_extract_dedupes_duplicate_ids():
    """F3: a model emitting duplicate ids must not drop a question."""
    dup = MockProvider(
        responses=['[{"id":"1","question_text":"Do you encrypt at rest?","answer_type":"yes_no"},'
                   '{"id":"1","question_text":"Do you enforce MFA?","answer_type":"yes_no"}]']
    )
    doc = load_document(FIX / "sample.xlsx")
    questions = extract_questions(doc, dup)
    assert len(questions) == 2
    ids = [q.id for q in questions]
    assert len(set(ids)) == 2, f"ids not unique: {ids}"
    # Both question texts survive.
    texts = {q.text for q in questions}
    assert "Do you encrypt at rest?" in texts
    assert "Do you enforce MFA?" in texts


def test_extract_retries_then_raises_on_garbage():
    bad = MockProvider(responses=["not json", "still not json"])
    doc = load_document(FIX / "sample.xlsx")
    try:
        extract_questions(doc, bad)
        assert False, "expected ValueError"
    except ValueError:
        pass
    assert len(bad.calls) == 2  # one retry
