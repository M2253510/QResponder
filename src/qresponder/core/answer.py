"""Answering — LLM call #2, batched (§14, §18).

Coerces model output into validated `AnswerResult`s and enforces the guardrails
that don't depend on the model behaving:
  * every ANSWERED result must carry >=1 citation, else it's downgraded to
    NEEDS_REVIEW/unsupported (§4.2) — the no-fabrication safety net;
  * confidence is rule-based (§4.8, §11): generated answers cap at MEDIUM in
    Phase 0 (no faithfulness check yet); Tier-1 reuse is HIGH (set by the
    orchestrator). Persistent parse failure -> NEEDS_REVIEW/parse_error.
"""

from __future__ import annotations

import logging

from ..kb.base import snippet_supported
from ..llm.base import LLMProvider
from ..llm import prompts
from ..models import (
    AnswerResult,
    AnswerType,
    Citation,
    Confidence,
    ReviewReason,
    Status,
)
from .parsing import parse_json_array

log = logging.getLogger("qresponder.answer")

_VALID_REASONS = {r.value for r in ReviewReason}


def _coerce_result(raw: dict, q: dict, kb_context: str) -> AnswerResult:
    qid = str(raw.get("question_id") or q.get("question_id") or "")
    qtext = q.get("question_text", "")
    atype = str(raw.get("answer_type", q.get("answer_type", "unknown"))).lower()
    if atype not in {t.value for t in AnswerType}:
        atype = q.get("answer_type", "unknown")

    citations = []
    for c in raw.get("citations") or []:
        if isinstance(c, dict) and c.get("snippet"):
            citations.append(
                Citation(source=str(c.get("source", "knowledge-base")), snippet=str(c["snippet"]))
            )

    # GUARDRAIL (F2): drop citations whose snippet is not actually drawn from the
    # supplied KB context — a model can emit a plausible-but-absent snippet. The
    # empty-citations case below then downgrades the result. Generated path only;
    # Tier-1 results never pass through here (their citation is the approved
    # answer itself), consistent with F5.
    citations = [c for c in citations if snippet_supported(c.snippet, kb_context)]

    status_raw = str(raw.get("status", "needs_review")).lower()
    status = Status.ANSWERED if status_raw == "answered" else Status.NEEDS_REVIEW

    reason_raw = str(raw.get("review_reason", "none")).lower()
    review_reason = ReviewReason(reason_raw) if reason_raw in _VALID_REASONS else ReviewReason.NONE

    answer_text = str(raw.get("answer", "") or "").strip()

    # GUARDRAIL (§4.2): an ANSWERED result with no citation is not trustworthy.
    if status == Status.ANSWERED and not citations:
        status = Status.NEEDS_REVIEW
        review_reason = ReviewReason.UNSUPPORTED

    # GUARDRAIL (§4.8 / §11): explainable, rule-based confidence.
    if status == Status.ANSWERED:
        confidence = Confidence.MEDIUM  # generated; HIGH is reserved for Tier-1
    else:
        confidence = Confidence.LOW
        if review_reason == ReviewReason.NONE:
            review_reason = ReviewReason.UNSUPPORTED

    missing = raw.get("missing_info")
    if status == Status.NEEDS_REVIEW and not missing:
        missing = "Not supported by the provided knowledge base."

    source_tier = raw.get("source_tier")
    try:
        source_tier = int(source_tier) if source_tier is not None else None
    except (TypeError, ValueError):
        source_tier = None

    return AnswerResult(
        question_id=qid,
        question_text=qtext,
        answer=answer_text,
        answer_type=AnswerType(atype),
        citations=citations,
        confidence=confidence,
        status=status,
        review_reason=review_reason,
        missing_info=missing if status == Status.NEEDS_REVIEW else None,
        source_tier=source_tier,
    )


def _parse_error_results(questions: list[dict]) -> list[AnswerResult]:
    out = []
    for q in questions:
        out.append(
            AnswerResult(
                question_id=str(q.get("question_id", "")),
                question_text=q.get("question_text", ""),
                answer="",
                answer_type=AnswerType(q.get("answer_type", "unknown")),
                citations=[],
                confidence=Confidence.LOW,
                status=Status.NEEDS_REVIEW,
                review_reason=ReviewReason.PARSE_ERROR,
                missing_info="Model output could not be parsed; needs manual answer.",
            )
        )
    return out


def answer_batch(
    provider: LLMProvider,
    kb_context: str,
    questions: list[dict],
) -> list[AnswerResult]:
    """Answer one batch of questions against the assembled KB context."""
    if not questions:
        return []
    system = prompts.ANSWER_SYSTEM
    user = prompts.build_answer_user(kb_context, questions)

    raw_items = None
    last_err = None
    for attempt in range(2):
        text = provider.complete(system, user, max_tokens=4096)
        try:
            raw_items = parse_json_array(text)
            break
        except ValueError as exc:
            last_err = exc
            log.warning("Answer parse failed (attempt %d): %s", attempt + 1, exc)

    if raw_items is None:
        log.error("Answer batch unparseable after retry: %s", last_err)
        return _parse_error_results(questions)

    by_id = {str(r.get("question_id")): r for r in raw_items if isinstance(r, dict)}
    results: list[AnswerResult] = []
    for q in questions:
        qid = str(q.get("question_id"))
        raw = by_id.get(qid)
        if raw is None:
            # Model dropped a question — flag rather than fabricate.
            results.extend(_parse_error_results([q]))
        else:
            results.append(_coerce_result(raw, q, kb_context))
    return results
