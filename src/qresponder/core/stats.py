"""Completion / analytics (Phase 10 D).

Aggregates a workspace's own run outputs (each run's results.json) into local
metrics: completion rate, auto-answer rate by confidence, flagged-by-reason, and
an explicitly-labeled time-saved ESTIMATE. Local read only — no DB, no telemetry,
nothing sent anywhere.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..models import QuestionnaireResult, Status


def workspace_stats(runs_dir, minutes_per_question: float = 10.0) -> dict:
    d = Path(runs_dir)
    total = answered = 0
    by_confidence: Counter = Counter()
    by_reason: Counter = Counter()
    n_runs = 0
    files = sorted(d.rglob("results.json")) if d.exists() else []
    for fp in files:
        try:
            qr = QuestionnaireResult.model_validate_json(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - skip an unreadable run, don't crash stats
            continue
        n_runs += 1
        for r in qr.results:
            total += 1
            if r.status == Status.ANSWERED:
                answered += 1
                by_confidence[r.confidence.value] += 1
            else:
                by_reason[r.review_reason.value] += 1

    flagged = total - answered
    high = by_confidence.get("high", 0)
    medium = by_confidence.get("medium", 0)
    return {
        "n_runs": n_runs,
        "total_questions": total,
        "answered": answered,
        "flagged": flagged,
        "completion_rate": round(answered / total, 3) if total else 0.0,
        "auto_answer_by_confidence": {
            "high": high, "medium": medium, "low": by_confidence.get("low", 0),
        },
        "auto_answer_rate_high_med": round((high + medium) / total, 3) if total else 0.0,
        "flagged_by_reason": dict(by_reason),
        "time_saved_minutes": round(answered * minutes_per_question, 1),
        "time_saved_note": (
            f"estimate: {answered} auto-answered × {minutes_per_question} min/question"
        ),
    }
