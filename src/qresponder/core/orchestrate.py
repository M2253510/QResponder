"""Orchestration — the hard engineering (§5.2).

Per question, in order:
  1. Tier-1 Answer Library first (the authority). A strong, in-scope match is
     reused and marked source_tier=1 / HIGH confidence — no model call.
  2. Attachment-type questions are flagged NEEDS_REVIEW/attachment_unresolved
     (Phase 0 does not resolve files; Phase 2 does) — never sent to the text
     answerer where a model might fabricate.
  3. Everything else is batched and answered from the assembled, tag-scoped,
     cited KB context.

Original question order is preserved in the output.
"""

from __future__ import annotations

import logging

from ..config import Config
from ..kb.in_context import InContextKB
from ..kb.library import AUTO_REUSE_THRESHOLD, AnswerLibrary
from ..llm.base import LLMProvider
from ..models import (
    AnswerResult,
    AnswerType,
    Citation,
    Confidence,
    Question,
    ReviewReason,
    Status,
)
from .answer import answer_batch

log = logging.getLogger("qresponder.orchestrate")


def _library_source(entry) -> str:
    src = "Answer Library"
    if entry.approved_by:
        src += f" (approved by {entry.approved_by}, v{entry.version})"
    return src


def _library_reuse_result(q: Question, entry, score: float) -> AnswerResult:
    """Near-exact match (score >= AUTO_REUSE_THRESHOLD): silent reuse at HIGH."""
    return AnswerResult(
        question_id=q.id,
        question_text=q.text,
        answer=entry.answer,
        answer_type=q.answer_type if q.answer_type != AnswerType.UNKNOWN else AnswerType.TEXT,
        citations=[Citation(source=_library_source(entry), snippet=entry.answer, faithful=True)],
        confidence=Confidence.HIGH,  # Tier-1 reuse (§11)
        status=Status.ANSWERED,
        review_reason=ReviewReason.NONE,
        source_tier=1,
    )


def _library_suggest_result(q: Question, entry, score: float) -> AnswerResult:
    """Close-but-not-exact match (F1): propose the approved answer, don't decide.

    The human sees the proposed reuse and confirms it fits before using it —
    this is the guard against meaning-flipping near-misses auto-reusing.
    """
    return AnswerResult(
        question_id=q.id,
        question_text=q.text,
        answer=entry.answer,  # proposed reuse, surfaced for the human
        answer_type=q.answer_type if q.answer_type != AnswerType.UNKNOWN else AnswerType.TEXT,
        citations=[Citation(source=_library_source(entry), snippet=entry.answer, faithful=True)],
        confidence=Confidence.LOW,
        status=Status.NEEDS_REVIEW,
        review_reason=ReviewReason.LIBRARY_CANDIDATE,
        source_tier=1,
        missing_info=(
            f"Possible Answer Library match (score {score:.2f}) — confirm this "
            "approved answer fits before using."
        ),
    )


def _attachment_result(q: Question) -> AnswerResult:
    return AnswerResult(
        question_id=q.id,
        question_text=q.text,
        answer="",
        answer_type=AnswerType.ATTACHMENT,
        citations=[],
        confidence=Confidence.LOW,
        status=Status.NEEDS_REVIEW,
        review_reason=ReviewReason.ATTACHMENT_UNRESOLVED,
        missing_info="Attachment requested. Resolve the document manually (Phase 2 automates this).",
    )


def _payload(q: Question) -> dict:
    return {
        "question_id": q.id,
        "question_text": q.text,
        "answer_type": q.answer_type.value,
    }


def orchestrate(
    questions: list[Question],
    provider: LLMProvider,
    library: AnswerLibrary,
    kb,
    config: Config,
    scope_tags=None,
) -> list[AnswerResult]:
    from .confidence import decide_confidence
    from .faithfulness import verify_results

    results: dict[str, AnswerResult] = {}
    to_generate: list[Question] = []
    retrieval_score: dict[str, float | None] = {}

    n_lib = 0
    n_suggest = 0
    n_attach = 0
    for q in questions:
        if q.answer_type == AnswerType.ATTACHMENT:
            results[q.id] = _attachment_result(q)
            n_attach += 1
            continue
        hit = library.match(q.text, scope_tags=scope_tags)
        if hit is not None:
            entry, score = hit
            if score >= AUTO_REUSE_THRESHOLD:
                results[q.id] = _library_reuse_result(q, entry, score)
                n_lib += 1
            else:
                results[q.id] = _library_suggest_result(q, entry, score)
                n_suggest += 1
            continue
        to_generate.append(q)

    retrieval_mode = config.kb_mode == "retrieval" and hasattr(kb, "retrieve")
    generated: list[AnswerResult] = []

    if retrieval_mode:
        # Per-question retrieval: each question gets its own reranked top-k
        # context. Small N — no need to force shared-context batching (§B1).
        for q in to_generate:
            hits = kb.retrieve(q.text, scope_tags=scope_tags)
            ctx = "\n\n".join(f"[source: {c.source}] {c.text}" for c, _ in hits)
            retrieval_score[q.id] = hits[0][1] if hits else None
            for r in answer_batch(provider, ctx, [_payload(q)]):
                results[r.question_id] = r
                generated.append(r)
    else:
        # In-context mode: one shared, tag-scoped context; batched answering.
        kb_context = kb.assemble_context(scope_tags=scope_tags, max_chars=config.max_kb_chars)
        batch_size = max(1, config.batch_size)
        for i in range(0, len(to_generate), batch_size):
            batch = to_generate[i : i + batch_size]
            for r in answer_batch(provider, kb_context, [_payload(q) for q in batch]):
                results[r.question_id] = r
                generated.append(r)
                retrieval_score.setdefault(r.question_id, None)

    # Faithfulness / citation verification (mutates generated results; F5 exempts
    # Tier-1, which never enters `generated`).
    verify_results(provider, generated, config)

    # Finalize explainable confidence from signals (§11).
    strong_threshold = getattr(config, "strong_rerank_score", 0.0)
    for r in generated:
        faithful = bool(r.citations) and all(c.faithful is True for c in r.citations)
        r.confidence = decide_confidence(
            source_tier=r.source_tier,
            status=r.status,
            faithful=faithful,
            retrieval_score=retrieval_score.get(r.question_id),
            strong_threshold=strong_threshold,
        )

    log.info(
        "Orchestrated: %d tier-1 reuse, %d library-candidate, %d attachment-flagged, %d generated (%s mode)",
        n_lib,
        n_suggest,
        n_attach,
        len(generated),
        "retrieval" if retrieval_mode else "in_context",
    )
    # Preserve original order.
    ordered: list[AnswerResult] = []
    for q in questions:
        if q.id in results:
            ordered.append(results[q.id])
    return ordered
