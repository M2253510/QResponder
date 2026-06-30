"""Completion / analytics tests (Phase 10 D). Local read only, offline."""

from pathlib import Path

import pytest

from qresponder.core.stats import workspace_stats
from qresponder.models import (
    AnswerResult,
    AnswerType,
    Confidence,
    QuestionnaireResult,
    ReviewReason,
    Status,
)


def _write_run(runs_dir, name, results):
    d = runs_dir / name
    d.mkdir(parents=True, exist_ok=True)
    qr = QuestionnaireResult(source_file=name + ".xlsx", results=results)
    (d / "results.json").write_text(qr.model_dump_json(indent=2), encoding="utf-8")


def _ans(conf):
    return AnswerResult(question_id="q", question_text="Q?", answer="A", answer_type=AnswerType.TEXT,
                        confidence=conf, status=Status.ANSWERED)


def _flag(reason):
    return AnswerResult(question_id="q", question_text="Q?", answer="", answer_type=AnswerType.TEXT,
                        confidence=Confidence.LOW, status=Status.NEEDS_REVIEW, review_reason=reason)


def test_workspace_stats_aggregates(tmp_path):
    runs = tmp_path / "runs"
    _write_run(runs, "run1", [_ans(Confidence.HIGH), _ans(Confidence.MEDIUM), _flag(ReviewReason.UNSUPPORTED)])
    _write_run(runs, "run2", [_ans(Confidence.HIGH), _flag(ReviewReason.AMBIGUOUS)])

    s = workspace_stats(runs, minutes_per_question=10.0)
    assert s["n_runs"] == 2
    assert s["total_questions"] == 5
    assert s["answered"] == 3 and s["flagged"] == 2
    assert s["completion_rate"] == 0.6
    assert s["auto_answer_by_confidence"] == {"high": 2, "medium": 1, "low": 0}
    assert s["auto_answer_rate_high_med"] == 0.6
    assert s["flagged_by_reason"] == {"unsupported": 1, "ambiguous": 1}
    # Time saved uses the configurable baseline and is labeled an estimate.
    assert s["time_saved_minutes"] == 30.0  # 3 answered * 10
    assert "estimate" in s["time_saved_note"]


def test_empty_workspace_stats(tmp_path):
    s = workspace_stats(tmp_path / "runs")
    assert s["n_runs"] == 0 and s["total_questions"] == 0
    assert s["completion_rate"] == 0.0


pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from qresponder.config import Config  # noqa: E402
from qresponder.web.app import create_app  # noqa: E402


def test_web_stats_endpoint(tmp_path):
    cfg = Config(llm_provider="mock", kb_mode="in_context", stats_minutes_per_question=5.0)
    cfg.extra["workspaces_dir"] = str(tmp_path / "ws")
    client = TestClient(create_app(cfg))
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    runs = Path(tmp_path) / "ws" / wid / "runs"
    _write_run(runs, "r1", [_ans(Confidence.HIGH), _flag(ReviewReason.UNSUPPORTED)])
    s = client.get(f"/api/workspaces/{wid}/stats").json()
    assert s["total_questions"] == 2 and s["answered"] == 1
    assert s["time_saved_minutes"] == 5.0  # 1 answered * 5 min
