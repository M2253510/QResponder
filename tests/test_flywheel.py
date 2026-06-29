"""Flywheel tests (C4, §10) — approve reviewed answers into the Library."""

from pathlib import Path

import yaml

from qresponder.core.flywheel import approve, approve_one
from qresponder.kb.library import AnswerLibrary
from qresponder.models import (
    AnswerResult,
    AnswerType,
    Confidence,
    QuestionnaireResult,
    Status,
)


def _result(qid, question, answer, status=Status.ANSWERED, atype=AnswerType.TEXT):
    return AnswerResult(
        question_id=qid,
        question_text=question,
        answer=answer,
        answer_type=atype,
        confidence=Confidence.HIGH,
        status=status,
    )


def _write_results(path, results):
    qr = QuestionnaireResult(source_file="x.xlsx", results=results)
    path.write_text(qr.model_dump_json(indent=2), encoding="utf-8")


def test_approve_appends_new_entries(tmp_path):
    res = tmp_path / "results.json"
    qa = tmp_path / "qa.yaml"
    _write_results(res, [
        _result("q1", "Do you encrypt data at rest?", "Yes, AES-256 at rest."),
        _result("q2", "Unsupported?", "", status=Status.NEEDS_REVIEW),  # not accepted
        _result("q3", "Attach SOC 2", "SOC2.pdf", atype=AnswerType.ATTACHMENT),  # excluded
    ])
    stats = approve(res, qa, approved_by="alice", extra_tags=["soc2", "encryption"])
    assert stats["added"] == 1
    assert stats["total"] == 1

    lib = AnswerLibrary.load(qa)
    assert len(lib.entries) == 1
    e = lib.entries[0]
    assert e.question == "Do you encrypt data at rest?"
    assert e.approved_by == "alice"
    assert e.version == 1
    assert "soc2" in e.tags and "encryption" in e.tags  # tags carry through


def test_reapprove_bumps_version_and_updates(tmp_path):
    res = tmp_path / "results.json"
    qa = tmp_path / "qa.yaml"

    _write_results(res, [_result("q1", "Do you encrypt data at rest?", "Yes, AES-256.")])
    approve(res, qa, approved_by="alice", extra_tags=["soc2"])

    # Same question, edited answer -> update + version bump, no duplicate.
    _write_results(res, [_result("q1", "Do you encrypt data at rest?", "Yes, AES-256 with KMS rotation.")])
    stats = approve(res, qa, approved_by="bob", extra_tags=["kms"])
    assert stats["updated"] == 1
    assert stats["added"] == 0

    lib = AnswerLibrary.load(qa)
    assert len(lib.entries) == 1  # de-duplicated, not appended
    e = lib.entries[0]
    assert e.version == 2
    assert e.answer == "Yes, AES-256 with KMS rotation."
    assert e.approved_by == "bob"
    assert "soc2" in e.tags and "kms" in e.tags


def test_approve_one_adds_then_updates(tmp_path):
    qa = tmp_path / "qa.yaml"
    r1 = approve_one("Do you encrypt data at rest?", "Yes, AES-256.", qa, approved_by="alice", tags=["soc2"])
    assert r1["action"] == "added"
    assert r1["version"] == 1
    assert r1["total"] == 1

    # Same question, edited answer -> update + version bump (no duplicate).
    r2 = approve_one("Do you encrypt data at rest?", "Yes, AES-256 with KMS.", qa, approved_by="bob", tags=["kms"])
    assert r2["action"] == "updated"
    assert r2["version"] == 2
    assert r2["total"] == 1

    lib = AnswerLibrary.load(qa)
    e = lib.entries[0]
    assert e.answer == "Yes, AES-256 with KMS."
    assert e.approved_by == "bob"
    assert "soc2" in e.tags and "kms" in e.tags


def test_approve_preserves_existing_entries(tmp_path):
    qa = tmp_path / "qa.yaml"
    qa.write_text(yaml.safe_dump([
        {"question": "Existing question?", "answer": "Existing answer.", "tags": ["x"], "version": 3}
    ]), encoding="utf-8")

    res = tmp_path / "results.json"
    _write_results(res, [_result("q1", "Brand new question?", "Brand new answer.")])
    approve(res, qa, approved_by="alice")

    lib = AnswerLibrary.load(qa)
    questions = {e.question for e in lib.entries}
    assert "Existing question?" in questions  # not lost
    assert "Brand new question?" in questions
