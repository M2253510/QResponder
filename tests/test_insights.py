"""KB Insights knowledge-gap report (Phase 15). Offline; built from run records."""

from pathlib import Path

import pytest

from qresponder.core.insights import kb_insights
from qresponder.models import (AnswerResult, AnswerType, Confidence, QuestionnaireResult,
                               ReviewReason, Status)


def _flag(q, reason=ReviewReason.UNSUPPORTED):
    return AnswerResult(question_id="q", question_text=q, answer="", answer_type=AnswerType.TEXT,
                        confidence=Confidence.LOW, status=Status.NEEDS_REVIEW, review_reason=reason)


def _ans(q, tier=None, answer="Yes."):
    return AnswerResult(question_id="q", question_text=q, answer=answer, answer_type=AnswerType.TEXT,
                        confidence=Confidence.HIGH, status=Status.ANSWERED, source_tier=tier)


def _write(runs, name, results):
    d = runs / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "results.json").write_text(
        QuestionnaireResult(source_file=name + ".xlsx", results=results).model_dump_json(), encoding="utf-8")


def test_gaps_grouped_and_ranked(tmp_path):
    runs = tmp_path / "runs"
    _write(runs, "run1", [
        _flag("What is your data retention period for backups?"),
        _flag("Do you have a backup retention schedule?"),
        _flag("Describe your penetration testing cadence.", ReviewReason.AMBIGUOUS),
        _ans("Do you encrypt data at rest?", tier=1),
    ])
    _write(runs, "run2", [
        _flag("How long is backup data retained?"),
        _ans("Do you encrypt data at rest?", tier=1),
    ])
    r = kb_insights(runs)
    assert r["n_runs"] == 2 and r["total_questions"] == 6 and r["flagged"] == 4
    # Gaps grouped by reason, ranked (unsupported=3 > ambiguous=1).
    reasons = [(g["reason"], g["count"]) for g in r["gaps_by_reason"]]
    assert reasons[0] == ("unsupported", 3)
    assert ("ambiguous", 1) in reasons
    # Keyword theme surfaces the recurring topic (backup/retention) with examples.
    themes = {t["theme"]: t["count"] for t in r["gap_themes"]}
    assert themes.get("backup", 0) >= 2 or themes.get("retention", 0) >= 2
    assert any(t["examples"] for t in r["gap_themes"])
    # Most-reused Tier-1 answer (the encrypt-at-rest answer used in both runs).
    assert r["reused_tier1"] and r["reused_tier1"][0]["count"] == 2


def test_series_is_per_run_abstain_rate(tmp_path):
    runs = tmp_path / "runs"
    _write(runs, "a", [_flag("x?"), _ans("y?")])          # 1/2 abstained
    _write(runs, "b", [_ans("p?"), _ans("q?"), _ans("r?")])  # 0/3 abstained
    r = kb_insights(runs)
    assert len(r["series"]) == 2
    rates = sorted(s["abstain_rate"] for s in r["series"])
    assert rates == [0.0, 0.5]


def test_empty_history(tmp_path):
    r = kb_insights(tmp_path / "runs")
    assert r["n_runs"] == 0 and r["gaps_by_reason"] == [] and r["abstain_rate"] == 0.0


# --- web (offline) ---
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from qresponder.config import Config  # noqa: E402
from qresponder.web.app import create_app  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
KB_MD = "Tags: soc2\n\nWe maintain a documented incident response plan, reviewed annually."


def _client(tmp_path):
    cfg = Config(llm_provider="mock", kb_mode="in_context")
    cfg.extra["workspaces_dir"] = str(tmp_path / "ws")
    return TestClient(create_app(cfg))


def test_home_reflects_real_state(tmp_path):
    client = _client(tmp_path)
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    # Fresh: nothing done.
    h = client.get(f"/api/workspaces/{wid}/home").json()
    assert h["setup"]["done"] == 0 and h["kb_docs"] == 0
    # Add a KB doc → step 1 done.
    client.post(f"/api/workspaces/{wid}/kb", files=[("files", ("kb.md", KB_MD, "text/markdown"))])
    # Ask → step 2 done (asked flag set).
    client.post(f"/api/workspaces/{wid}/ask", json={"question": "Do you have an incident response plan?", "tags": "soc2"})
    h = client.get(f"/api/workspaces/{wid}/home").json()
    steps = {s["key"]: s["done"] for s in h["setup"]["steps"]}
    assert steps["document"] is True and steps["ask"] is True
    assert steps["automate"] is False  # no run yet
    assert h["setup"]["done"] == 2


def test_insights_endpoint_and_csv_export(tmp_path):
    client = _client(tmp_path)
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    runs = Path(tmp_path) / "ws" / wid / "runs"
    _write(runs, "r1", [_flag("What is your backup retention period?"), _flag("Backup retention schedule?")])
    j = client.get(f"/api/workspaces/{wid}/insights").json()
    assert j["flagged"] == 2 and j["gaps_by_reason"][0]["reason"] == "unsupported"
    csv = client.get(f"/api/workspaces/{wid}/insights/export?fmt=csv")
    assert csv.status_code == 200 and "gap_reason" in csv.text and "unsupported" in csv.text
