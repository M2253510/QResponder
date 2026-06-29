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


def _ambiguous_result(q: Question, candidates) -> AnswerResult:
    """Ambiguous question (C1, §8): surface every interpretation's grounded draft
    for the human to pick. Never collapse to one reading."""
    return AnswerResult(
        question_id=q.id,
        question_text=q.text,
        answer="",
        answer_type=q.answer_type if q.answer_type != AnswerType.UNKNOWN else AnswerType.TEXT,
        citations=[],
        confidence=Confidence.LOW,
        status=Status.NEEDS_REVIEW,
        review_reason=ReviewReason.AMBIGUOUS,
        candidates=candidates,
        missing_info=f"Ambiguous — {len(candidates)} interpretations; choose one.",
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
    evidence=None,
) -> list[AnswerResult]:
    from ..models import AuditTrail, RetrievedCandidate
    from .confidence import confidence_rationale, decide_confidence, grounding_score
    from .conflicts import detect_conflicts
    from .faithfulness import verify_results
    from .interpretations import answer_interpretations

    results: dict[str, AnswerResult] = {}
    to_generate: list[Question] = []
    ambiguous_questions: list[Question] = []
    retrieval_score: dict[str, float | None] = {}
    retrieved_map: dict[str, list] = {}  # qid -> [RetrievedCandidate] (audit, Part B)

    retrieval_mode = config.kb_mode == "retrieval" and hasattr(kb, "retrieve")
    shared_ctx = None
    if not retrieval_mode:
        shared_ctx = kb.assemble_context(scope_tags=scope_tags, max_chars=config.max_kb_chars)

    def ctx_for(q: Question):
        """(context, top_score) for a question — per-question in retrieval mode,
        the shared assembled context otherwise."""
        if retrieval_mode:
            hits = kb.retrieve(q.text, scope_tags=scope_tags)
            ctx = "\n\n".join(f"[source: {c.source}] {c.text}" for c, _ in hits)
            return ctx, (hits[0][1] if hits else None)
        return shared_ctx, None

    n_lib = n_suggest = n_attach = n_ambig = 0
    for q in questions:
        if q.answer_type == AnswerType.ATTACHMENT:
            if evidence is not None:
                from .attachments import resolve_attachment

                results[q.id] = resolve_attachment(q, evidence, config, scope_tags=scope_tags)
            else:
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
        if q.ambiguous and len(q.interpretations) >= 2:
            ambiguous_questions.append(q)
            continue
        to_generate.append(q)

    # Ambiguous questions: draft one grounded answer per interpretation (C1).
    for q in ambiguous_questions:
        ctx, _ = ctx_for(q)
        candidates = answer_interpretations(provider, q.text, q.interpretations, ctx)
        results[q.id] = _ambiguous_result(q, candidates)
        n_ambig += 1

    generated: list[AnswerResult] = []
    if retrieval_mode:
        # Per-question retrieval: each question gets its own reranked top-k
        # context. Small N — no need to force shared-context batching (§B1).
        for q in to_generate:
            hits = kb.retrieve(q.text, scope_tags=scope_tags)
            ctx = "\n\n".join(f"[source: {c.source}] {c.text}" for c, _ in hits)
            retrieval_score[q.id] = hits[0][1] if hits else None
            retrieved_map[q.id] = [
                RetrievedCandidate(source=c.source, snippet=c.text, score=round(float(s), 4))
                for c, s in hits
            ]
            for r in answer_batch(provider, ctx, [_payload(q)]):
                results[r.question_id] = r
                generated.append(r)
    else:
        # In-context mode: one shared, tag-scoped context; batched answering.
        batch_size = max(1, config.batch_size)
        for i in range(0, len(to_generate), batch_size):
            batch = to_generate[i : i + batch_size]
            for r in answer_batch(provider, shared_ctx, [_payload(q) for q in batch]):
                results[r.question_id] = r
                generated.append(r)
                retrieval_score.setdefault(r.question_id, None)

    # Faithfulness / citation verification (mutates generated results; F5 exempts
    # Tier-1, which never enters `generated`).
    verify_results(provider, generated, config)

    # Finalize explainable confidence from signals (§11, S1).
    for r in generated:
        faithful = bool(r.citations) and all(c.faithful is True for c in r.citations)
        if retrieval_mode:
            score = retrieval_score.get(r.question_id)
            threshold = getattr(config, "strong_rerank_score", 0.0)
        else:
            # In-context: derive a real grounding signal so HIGH is reachable
            # when genuinely supported, not blocked by the missing reranker.
            score = grounding_score(r.answer, [c.snippet for c in r.citations]) if r.citations else None
            threshold = getattr(config, "strong_grounding_score", 0.85)
        r.grounding_score = score
        r.confidence = decide_confidence(
            source_tier=r.source_tier,
            status=r.status,
            faithful=faithful,
            retrieval_score=score,
            strong_threshold=threshold,
        )
        # Audit trail (Part B): capture the evidence chain while it's live.
        r.audit = AuditTrail(
            retrieved=retrieved_map.get(r.question_id, []),
            cited=list(r.citations),
            faithfulness={
                "passed": faithful,
                "reason": "all cited claims entailed" if faithful else "no faithful citation",
            },
            confidence_rationale=confidence_rationale(
                confidence=r.confidence, source_tier=r.source_tier,
                faithful=faithful, retrieval_score=score, strong_threshold=threshold,
            ),
        )

    # Cross-source conflict detection (D1): compare answered results against the
    # Library and each other; flag clear contradictions for human reconciliation.
    detect_conflicts(list(results.values()), library, provider, config)

    # Ensure every result (Tier-1 reuse, attachments, ambiguous) carries an audit
    # trail for the evidence pack — capture, not new logic.
    for r in results.values():
        if r.audit is None:
            r.audit = AuditTrail(
                cited=list(r.citations),
                faithfulness={"passed": r.source_tier == 1, "reason": (
                    "Tier-1 approved (exempt from judge)" if r.source_tier == 1 else "not generated/verified")},
                confidence_rationale=confidence_rationale(
                    confidence=r.confidence, source_tier=r.source_tier,
                    faithful=(r.source_tier == 1), retrieval_score=r.grounding_score,
                ),
            )

    log.info(
        "Orchestrated: %d tier-1 reuse, %d library-candidate, %d ambiguous, "
        "%d attachment, %d generated (%s mode)",
        n_lib,
        n_suggest,
        n_ambig,
        n_attach,
        len(generated),
        "retrieval" if retrieval_mode else "in_context",
    )
    # Carry write-back anchors from the question onto its result (C3), so the
    # output layer can fill the original template without re-deriving them.
    qmap = {q.id: q for q in questions}
    for r in results.values():
        q = qmap.get(r.question_id)
        if q is not None:
            r.location_hint = q.location_hint
            r.answer_location_hint = q.answer_location_hint

    # Preserve original order.
    ordered: list[AnswerResult] = []
    for q in questions:
        if q.id in results:
            ordered.append(results[q.id])
    return ordered
