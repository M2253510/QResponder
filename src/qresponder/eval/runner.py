"""Eval harness runner (§11, §16.14) — `qresponder eval`.

Runs each golden item through the REAL answer path (library -> retrieval/in-context
-> answer -> faithfulness) against the configured KB + model, then reports:
  * Recall@K     — was expected_source in the retrieved top-k (retrieval mode);
  * faithfulness — fraction of answered items whose citations are faithful;
  * correctness  — LLM-judge coverage of key_facts (calibrate against a small
                   human-graded baseline; judges hallucinate too);
  * coverage     — % auto-answered vs % flagged, broken down by reason.

This turns "is my local model good enough?" into numbers. All calls go through
the configured provider, so the local path stays offline.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

import yaml

from ..config import Config
from ..kb.library import AnswerLibrary
from ..kb.tags import normalize_tags
from ..llm.base import LLMProvider, make_provider
from ..llm import prompts
from ..core.orchestrate import orchestrate
from ..core.parsing import parse_json_array
from ..models import AnswerType, Question, Status
from .metrics import EvalItemResult, EvalReport

log = logging.getLogger("qresponder.eval")

_CALIBRATION_NOTE = (
    "Correctness is LLM-judged on key-fact coverage — calibrate it against a "
    "small human-graded baseline before trusting it; judges hallucinate too."
)


def _load_eval(path: str | Path) -> list[dict]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    return [x for x in raw if isinstance(x, dict) and x.get("question")]


def _grade_correctness(provider: LLMProvider, graded_items: list[dict]) -> dict[str, dict]:
    """One judge call grading key-fact coverage for all answered+keyed items."""
    if not graded_items:
        return {}
    try:
        text = provider.complete(
            prompts.EVAL_CORRECTNESS_SYSTEM,
            prompts.build_eval_correctness_user(graded_items),
            max_tokens=2048,
        )
        verdicts = {}
        for v in parse_json_array(text):
            if isinstance(v, dict) and "id" in v:
                verdicts[str(v["id"])] = v
        return verdicts
    except Exception as exc:  # noqa: BLE001
        log.warning("Correctness judge failed: %s", exc)
        return {}


def run_eval(
    eval_path: str | Path,
    kb_dir: str | None,
    qa_path: str | None,
    config: Config,
    provider: LLMProvider | None = None,
    kb=None,
) -> EvalReport:
    provider = provider or make_provider(config)
    items = _load_eval(eval_path)
    library = AnswerLibrary.load(qa_path)

    if kb is None:
        if config.kb_mode == "retrieval":
            from ..kb.retrieval import RetrievalKB

            kb = RetrievalKB.load(kb_dir, config)
        else:
            from ..kb.in_context import InContextKB

            kb = InContextKB.load(kb_dir)

    item_results: list[EvalItemResult] = []
    grading_payload: list[dict] = []  # for the correctness judge

    for i, item in enumerate(items):
        qtext = str(item["question"])
        tags = normalize_tags(item.get("tags"))
        expected_source = item.get("expected_source")
        key_facts = [str(f) for f in (item.get("key_facts") or [])]

        # Recall@K (retrieval mode only, when an expected source is given).
        recall_hit = None
        if expected_source and hasattr(kb, "retrieve"):
            hits = kb.retrieve(qtext, scope_tags=tags)
            sources = {c.source for c, _ in hits}
            recall_hit = expected_source in sources

        # Run the real answer path for this item.
        q = Question(id=f"e{i}", text=qtext, answer_type=AnswerType.TEXT)
        result = orchestrate([q], provider, library, kb, config, scope_tags=tags)[0]

        faithful = None
        if result.status == Status.ANSWERED and result.citations:
            faithful = all(c.faithful is True for c in result.citations)

        ir = EvalItemResult(
            question=qtext,
            status=result.status.value,
            review_reason=result.review_reason.value,
            confidence=result.confidence.value,
            source_tier=result.source_tier,
            answer=result.answer,
            recall_hit=recall_hit,
            faithful=faithful,
        )
        item_results.append(ir)

        if key_facts and result.status == Status.ANSWERED:
            grading_payload.append({"id": f"e{i}", "answer": result.answer, "key_facts": key_facts})
            ir.missing_facts = key_facts  # provisional; refined below

    # Correctness grading (batched judge call).
    verdicts = _grade_correctness(provider, grading_payload)
    correctness_scores: list[float] = []
    for i, (ir, item) in enumerate(zip(item_results, items)):
        key_facts = [str(f) for f in (item.get("key_facts") or [])]
        if not key_facts:
            ir.missing_facts = []
            continue
        v = verdicts.get(f"e{i}")  # ids assigned as eN over the same enumerate
        if v is None:
            ir.correctness = None
            continue
        covered = [str(x) for x in (v.get("covered_facts") or [])]
        missing = [str(x) for x in (v.get("missing_facts") or [])]
        ir.covered_facts = covered
        ir.missing_facts = missing
        ir.correctness = len(covered) / len(key_facts)
        correctness_scores.append(ir.correctness)

    # Aggregate metrics.
    n = len(item_results)
    recall_items = [r for r in item_results if r.recall_hit is not None]
    recall_at_k = (
        sum(1 for r in recall_items if r.recall_hit) / len(recall_items)
        if recall_items
        else None
    )
    answered = [r for r in item_results if r.status == Status.ANSWERED.value]
    faithful_items = [r for r in answered if r.faithful is not None]
    faithfulness_rate = (
        sum(1 for r in faithful_items if r.faithful) / len(faithful_items)
        if faithful_items
        else None
    )
    correctness = (sum(correctness_scores) / len(correctness_scores)) if correctness_scores else None

    n_answered = len(answered)
    n_flagged = n - n_answered
    by_reason = Counter(r.review_reason for r in item_results if r.status != Status.ANSWERED.value)
    coverage = {
        "answered": n_answered,
        "flagged": n_flagged,
        "auto_pct": round(100 * n_answered / n, 1) if n else 0.0,
        "flagged_pct": round(100 * n_flagged / n, 1) if n else 0.0,
        "by_reason": dict(by_reason),
    }

    return EvalReport(
        n_items=n,
        k=config.top_k_context,
        recall_at_k=recall_at_k,
        faithfulness_rate=faithfulness_rate,
        correctness=correctness,
        coverage=coverage,
        items=item_results,
        note=_CALIBRATION_NOTE,
    )


def format_report(report: EvalReport) -> str:
    """Compact text table for the CLI."""
    def pct(x):
        return "n/a" if x is None else f"{100 * x:.1f}%"

    lines = [
        "QRESPONDER eval",
        f"  items            : {report.n_items}",
        f"  Recall@{report.k}         : {pct(report.recall_at_k)}",
        f"  faithfulness     : {pct(report.faithfulness_rate)}",
        f"  correctness      : {pct(report.correctness)}  (key-fact coverage)",
        f"  auto-answered    : {report.coverage.get('auto_pct', 0)}%",
        f"  flagged          : {report.coverage.get('flagged_pct', 0)}%",
    ]
    by_reason = report.coverage.get("by_reason", {})
    if by_reason:
        lines.append("  flagged by reason:")
        for reason, count in by_reason.items():
            lines.append(f"    - {reason}: {count}")
    lines.append(f"  note: {report.note}")
    return "\n".join(lines)
