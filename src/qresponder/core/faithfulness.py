"""Faithfulness / citation verification (§4.3, §11) — Phase 1.

For each GENERATED answered result, verify every factual claim in the answer is
entailed by its cited snippet(s) — not merely topically related (the
"grounded-but-wrong" gap that plain topical faithfulness misses). An LLM-judge
call (via the same provider, so the local path stays offline) returns a verdict;
we set Citation.faithful and, on failure, downgrade to NEEDS_REVIEW /
FAITHFULNESS_FAIL.

F5 — Tier-1 exemption: results with source_tier == 1 are grounded by human
approval (their citation IS the approved answer). They are NEVER sent to the
judge; their citations are marked faithful=True directly. The orchestrator sets
this at construction; this module simply skips them.
"""

from __future__ import annotations

import logging

from ..llm.base import LLMProvider
from ..llm import prompts
from ..models import AnswerResult, Confidence, ReviewReason, Status
from .parsing import parse_json_array

log = logging.getLogger("qresponder.faithfulness")


def _is_generated_answered(r: AnswerResult) -> bool:
    return r.status == Status.ANSWERED and r.source_tier != 1 and bool(r.citations)


def verify_results(
    provider: LLMProvider,
    results: list[AnswerResult],
    config,
) -> list[AnswerResult]:
    """Verify generated answered results in one batched judge call.

    Mutates and returns the same list. Tier-1 results are skipped (F5). If the
    check is disabled, generated citations are left unverified (faithful=None).
    """
    if not getattr(config, "verify_faithfulness", True):
        return results

    targets = [r for r in results if _is_generated_answered(r)]
    if not targets:
        return results

    items = [
        {
            "id": r.question_id,
            "answer": r.answer,
            "snippets": [c.snippet for c in r.citations],
        }
        for r in targets
    ]

    verdicts: dict[str, dict] = {}
    try:
        text = provider.complete(
            prompts.FAITHFULNESS_SYSTEM, prompts.build_faithfulness_user(items), max_tokens=2048
        )
        for v in parse_json_array(text):
            if isinstance(v, dict) and "id" in v:
                verdicts[str(v["id"])] = v
    except Exception as exc:  # noqa: BLE001
        # Conservative on judge failure: leave faithful=None, do not upgrade.
        log.warning("Faithfulness judge failed (%s); leaving results unverified.", exc)
        return results

    for r in targets:
        v = verdicts.get(r.question_id)
        if v is None:
            continue  # no verdict -> leave unverified (faithful stays None)
        faithful = bool(v.get("faithful", False))
        for c in r.citations:
            c.faithful = faithful
        if not faithful:
            r.status = Status.NEEDS_REVIEW
            r.review_reason = ReviewReason.FAITHFULNESS_FAIL
            r.confidence = Confidence.LOW
            unsupported = v.get("unsupported_claims") or []
            detail = "; ".join(str(x) for x in unsupported) if unsupported else "claims not entailed by cited snippets"
            r.missing_info = f"Faithfulness check failed: {detail}."
    return results
