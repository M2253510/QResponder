"""KB Insights — a knowledge-gap report (Phase 15).

Distinct from the Phase-10D usage stats: this analyzes a workspace's own run
history (each run's results.json) to surface **where the KB can't yet answer** —
the questions that abstained or were flagged, grouped by reason and by keyword
theme, the auto-answer-vs-abstain rate per run (a trend), and the most-reused
Tier-1 (approved-library) answers. Local read only — no DB, no telemetry.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from ..models import QuestionnaireResult, Status

# Small stopword set so keyword themes surface real topics, not filler.
_STOP = {
    "the", "a", "an", "is", "are", "do", "does", "did", "you", "your", "yours", "we", "our",
    "have", "has", "had", "of", "to", "in", "on", "for", "and", "or", "with", "any", "all",
    "how", "what", "when", "where", "which", "who", "will", "can", "please", "provide", "describe",
    "list", "detail", "there", "this", "that", "these", "those", "be", "been", "if", "at", "by",
    "as", "it", "its", "use", "used", "using", "about", "from", "into", "per", "not", "no", "yes",
}
_WORD = re.compile(r"[a-z][a-z0-9\-]{2,}")


def _keywords(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if w not in _STOP]


def kb_insights(runs_dir, top: int = 10, examples: int = 3) -> dict:
    """Aggregate run outputs into a knowledge-gap report."""
    d = Path(runs_dir)
    files = sorted(d.rglob("results.json"), key=lambda p: p.stat().st_mtime) if d.exists() else []

    total = answered = flagged = 0
    by_reason: Counter = Counter()
    reason_examples: dict[str, list[str]] = defaultdict(list)
    theme_counts: Counter = Counter()
    theme_examples: dict[str, list[str]] = defaultdict(list)
    tier1_counts: Counter = Counter()
    series: list[dict] = []

    for fp in files:
        try:
            qr = QuestionnaireResult.model_validate_json(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - skip an unreadable run, don't crash the report
            continue
        r_total = len(qr.results)
        r_flag = sum(1 for r in qr.results if r.status == Status.NEEDS_REVIEW)
        r_ans = r_total - r_flag
        series.append({
            "run": fp.parent.name, "source_file": qr.source_file,
            "total": r_total, "answered": r_ans, "flagged": r_flag,
            "abstain_rate": round(r_flag / r_total, 3) if r_total else 0.0,
        })
        total += r_total
        answered += r_ans
        flagged += r_flag
        for r in qr.results:
            if r.status == Status.NEEDS_REVIEW:
                reason = r.review_reason.value
                by_reason[reason] += 1
                if r.question_text not in reason_examples[reason]:
                    reason_examples[reason].append(r.question_text)
                for kw in set(_keywords(r.question_text)):
                    theme_counts[kw] += 1
                    if r.question_text not in theme_examples[kw]:
                        theme_examples[kw].append(r.question_text)
            elif r.status == Status.ANSWERED and (r.source_tier or 0) == 1 and r.answer:
                tier1_counts[r.answer.strip()[:100]] += 1

    gaps = [{"reason": reason, "count": count, "examples": reason_examples[reason][:examples]}
            for reason, count in by_reason.most_common()]
    themes = [{"theme": kw, "count": count, "examples": theme_examples[kw][:examples]}
              for kw, count in theme_counts.most_common(top) if count >= 2]
    reused = [{"answer": ans, "count": count} for ans, count in tier1_counts.most_common(top) if count >= 2]

    return {
        "n_runs": len(series),
        "total_questions": total,
        "answered": answered,
        "flagged": flagged,
        "abstain_rate": round(flagged / total, 3) if total else 0.0,
        "gaps_by_reason": gaps,          # what the KB couldn't answer, by reason
        "gap_themes": themes,            # keyword themes across flagged questions
        "reused_tier1": reused,          # the approved answers pulling the most weight
        "series": series,               # per-run abstain-rate trend (oldest → newest)
        "note": "Gaps are questions your KB couldn't yet answer — add content on these topics.",
    }
