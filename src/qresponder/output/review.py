"""Human-first review report (§15, guardrail §4.4).

review.md surfaces NEEDS_REVIEW and LOW-confidence items FIRST, grouped by
reason, so a human starts where attention is actually needed. Output is a draft;
there is no submit step.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..models import Confidence, QuestionnaireResult, ReviewReason, Status

_REASON_TITLES = {
    ReviewReason.AMBIGUOUS: "Ambiguous — multiple interpretations",
    ReviewReason.UNSUPPORTED: "Unsupported by the knowledge base",
    ReviewReason.FAITHFULNESS_FAIL: "Failed faithfulness check",
    ReviewReason.PARSE_ERROR: "Model output could not be parsed",
    ReviewReason.ATTACHMENT_UNRESOLVED: "Attachment requested — resolve a document",
    ReviewReason.LIBRARY_CANDIDATE: "Possible Answer Library match — confirm reuse",
    ReviewReason.NONE: "Flagged for review",
}


def build_review_md(result: QuestionnaireResult) -> str:
    total = len(result.results)
    needs_review = [r for r in result.results if r.status == Status.NEEDS_REVIEW]
    answered = [r for r in result.results if r.status == Status.ANSWERED]
    low_conf = [r for r in answered if r.confidence == Confidence.LOW]

    lines: list[str] = []
    lines.append(f"# Review report — {Path(result.source_file).name}")
    lines.append("")
    lines.append(
        f"**{total}** questions · **{len(answered)}** answered · "
        f"**{len(needs_review)}** need review · **{len(low_conf)}** low-confidence answered"
    )
    lines.append("")
    lines.append("> This is a **draft**. Nothing is submitted. Review the items below first.")
    lines.append("")

    # 1. Needs review, grouped by reason.
    if needs_review:
        grouped: dict[ReviewReason, list] = defaultdict(list)
        for r in needs_review:
            grouped[r.review_reason].append(r)
        lines.append("## ⚠️ Needs review")
        lines.append("")
        for reason, items in grouped.items():
            lines.append(f"### {_REASON_TITLES.get(reason, reason.value)} ({len(items)})")
            lines.append("")
            for r in items:
                lines.append(f"- **{r.question_text}**")
                if r.missing_info:
                    lines.append(f"  - Missing: {r.missing_info}")
            lines.append("")

    # 2. Low-confidence answered.
    if low_conf:
        lines.append("## 🟡 Low-confidence answers (verify)")
        lines.append("")
        for r in low_conf:
            lines.append(f"- **{r.question_text}**")
            lines.append(f"  - {r.answer}")
        lines.append("")

    # 3. Answered (for completeness, last).
    confident = [r for r in answered if r.confidence != Confidence.LOW]
    if confident:
        lines.append("## ✅ Answered")
        lines.append("")
        for r in confident:
            tier = f" (Tier {r.source_tier})" if r.source_tier else ""
            lines.append(f"- **{r.question_text}**{tier} — _{r.confidence.value}_")
            lines.append(f"  - {r.answer}")
            for c in r.citations:
                snippet = c.snippet if len(c.snippet) <= 160 else c.snippet[:157] + "..."
                lines.append(f"  - cite [{c.source}]: {snippet}")
        lines.append("")

    return "\n".join(lines)


def write_review_md(result: QuestionnaireResult, path: str | Path) -> Path:
    p = Path(path)
    p.write_text(build_review_md(result), encoding="utf-8")
    return p
