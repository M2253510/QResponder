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
from ..kb.base import lexical_similarity
from ..kb.library import AnswerLibrary
from ..kb.tags import normalize_tags
from ..llm.base import LLMProvider, make_provider
from ..llm import prompts
from ..core.orchestrate import orchestrate
from ..core.parsing import parse_json_array
from ..models import AnswerType, Question, Status
from .metrics import EvalItemResult, EvalReport


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None

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

        # Recall@K + MRR (retrieval mode only, when an expected source is given).
        recall_hit = None
        recall_rank = None
        if expected_source and hasattr(kb, "retrieve"):
            hits = kb.retrieve(qtext, scope_tags=tags)
            ordered_sources = [c.source for c, _ in hits]
            recall_hit = expected_source in ordered_sources
            if recall_hit:
                recall_rank = ordered_sources.index(expected_source) + 1

        # Run the real answer path for this item.
        q = Question(id=f"e{i}", text=qtext, answer_type=AnswerType.TEXT)
        result = orchestrate([q], provider, library, kb, config, scope_tags=tags)[0]

        faithful = None
        if result.status == Status.ANSWERED and result.citations:
            faithful = all(c.faithful is True for c in result.citations)

        # RAGAS-aligned deterministic proxies (offline; no judge needed).
        answer_relevancy = None
        context_precision = None
        context_recall = None
        if result.status == Status.ANSWERED and result.answer:
            answer_relevancy = round(lexical_similarity(qtext, result.answer), 3)
        if expected_source and result.citations:
            hits_n = sum(1 for c in result.citations if c.source == expected_source)
            context_precision = round(hits_n / len(result.citations), 3)
        if key_facts and result.citations:
            cited_blob = " ".join(c.snippet for c in result.citations).lower()
            present = sum(1 for f in key_facts if f.lower() in cited_blob)
            context_recall = round(present / len(key_facts), 3)

        ir = EvalItemResult(
            question=qtext,
            status=result.status.value,
            review_reason=result.review_reason.value,
            confidence=result.confidence.value,
            source_tier=result.source_tier,
            answer=result.answer,
            recall_hit=recall_hit,
            recall_rank=recall_rank,
            faithful=faithful,
            answer_relevancy=answer_relevancy,
            context_precision=context_precision,
            context_recall=context_recall,
            grounding_score=result.grounding_score,
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
    # MRR over items with an expected source (0 contribution when not retrieved).
    mrr = (
        sum((1.0 / r.recall_rank) if r.recall_rank else 0.0 for r in recall_items) / len(recall_items)
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
    answer_relevancy = _mean([r.answer_relevancy for r in item_results])
    context_precision = _mean([r.context_precision for r in item_results])
    context_recall = _mean([r.context_recall for r in item_results])

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
    abstention = {"rate": round(n_flagged / n, 3) if n else 0.0, "by_reason": dict(by_reason)}

    # Calibration: measured correctness per predicted-confidence bucket. Proves
    # "HIGH means HIGH" — HIGH-bucket correctness should exceed MED/LOW.
    calibration = {}
    for bucket in ("high", "medium", "low"):
        scores = [r.correctness for r in answered if r.confidence == bucket and r.correctness is not None]
        calibration[bucket] = {
            "n": sum(1 for r in answered if r.confidence == bucket),
            "graded": len(scores),
            "correctness": round(sum(scores) / len(scores), 3) if scores else None,
        }

    score_distribution, suggested_threshold = _score_distribution(item_results)

    return EvalReport(
        n_items=n,
        k=config.top_k_context,
        faithfulness_rate=faithfulness_rate,
        answer_relevancy=answer_relevancy,
        context_precision=context_precision,
        context_recall=context_recall,
        correctness=correctness,
        recall_at_k=recall_at_k,
        mrr=mrr,
        coverage=coverage,
        abstention=abstention,
        calibration=calibration,
        score_distribution=score_distribution,
        suggested_threshold=suggested_threshold,
        items=item_results,
        note=_CALIBRATION_NOTE,
    )


def _stats(scores: list[float]) -> dict | None:
    if not scores:
        return None
    return {
        "n": len(scores),
        "min": round(min(scores), 3),
        "mean": round(sum(scores) / len(scores), 3),
        "max": round(max(scores), 3),
    }


def _score_distribution(items: list[EvalItemResult]):
    """Grounding/rerank score spread for answered vs flagged, plus a suggested
    threshold that best separates them (S2). Reranker-dependent — informational."""
    answered = [r.grounding_score for r in items
                if r.status == Status.ANSWERED.value and r.grounding_score is not None]
    flagged = [r.grounding_score for r in items
               if r.status != Status.ANSWERED.value and r.grounding_score is not None]
    dist = {"answered": _stats(answered), "flagged": _stats(flagged)}
    # Suggested threshold: midpoint between the answered floor and flagged ceiling
    # when they separate cleanly; else the mean of answered scores.
    suggested = None
    if answered and flagged:
        lo, hi = min(answered), max(flagged)
        suggested = round((lo + hi) / 2, 3)
    elif answered:
        suggested = round(sum(answered) / len(answered), 3)
    return dist, suggested


def format_report(report: EvalReport) -> str:
    """Compact text table for the CLI."""
    def pct(x):
        return "n/a" if x is None else f"{100 * x:.1f}%"

    def num(x):
        return "n/a" if x is None else f"{x:.3f}"

    lines = [
        "QRESPONDER eval",
        f"  items            : {report.n_items}",
        "  RAGAS-aligned:",
        f"    faithfulness     : {pct(report.faithfulness_rate)}",
        f"    answer_relevancy : {num(report.answer_relevancy)}",
        f"    context_precision: {num(report.context_precision)}",
        f"    context_recall   : {num(report.context_recall)}",
        f"    correctness      : {pct(report.correctness)}  (key-fact coverage)",
        "  retrieval:",
        f"    Recall@{report.k}        : {pct(report.recall_at_k)}",
        f"    MRR             : {num(report.mrr)}",
        "  abstention (restraint is the product):",
        f"    rate            : {pct(report.abstention.get('rate'))}",
    ]
    for reason, count in (report.abstention.get("by_reason") or {}).items():
        lines.append(f"    - {reason}: {count}")
    lines.append("  calibration (measured correctness per predicted confidence):")
    for bucket in ("high", "medium", "low"):
        c = (report.calibration or {}).get(bucket, {})
        lines.append(
            f"    {bucket:<6}: n={c.get('n', 0)} graded={c.get('graded', 0)} "
            f"correctness={pct(c.get('correctness'))}"
        )

    dist = report.score_distribution or {}
    ans, flg = dist.get("answered"), dist.get("flagged")
    if ans or flg:
        lines.append("  grounding/rerank score distribution:")
        if ans:
            lines.append(f"    answered: min={ans['min']} mean={ans['mean']} max={ans['max']} (n={ans['n']})")
        if flg:
            lines.append(f"    flagged : min={flg['min']} mean={flg['mean']} max={flg['max']} (n={flg['n']})")
        if report.suggested_threshold is not None:
            lines.append(
                f"  suggested strong-score threshold: {report.suggested_threshold} "
                "(reranker-dependent; set strong_rerank_score / strong_grounding_score)"
            )
    lines.append(f"  note: {report.note}")
    return "\n".join(lines)
