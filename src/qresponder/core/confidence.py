"""Explainable, rule-based confidence (§4.8, §11) — Phase 1.

No model-emitted percentages. Confidence is derived from signals we can name:
  * Tier-1 approved-library reuse                              -> HIGH
  * generated, faithfulness PASSED, strong rerank score       -> HIGH
  * generated, otherwise (e.g. weak/absent retrieval signal)  -> MEDIUM
  * not answered (faithfulness fail / unsupported / ambiguous
    / parse error / weak retrieval)                           -> LOW

This supersedes the Phase-0 "generated caps at MEDIUM": a generated answer can
now reach HIGH, but only via a passed faithfulness check AND a strong retrieval
(rerank) signal.
"""

from __future__ import annotations

from ..models import Confidence, Status

# Cross-encoder rerank threshold for "strong". bge-reranker / ms-marco-MiniLM
# emit a relevance logit where > 0 ≈ relevant; tune via config if needed.
DEFAULT_STRONG_RERANK_SCORE = 0.0


def is_strong_retrieval(retrieval_score: float | None, threshold: float = DEFAULT_STRONG_RERANK_SCORE) -> bool:
    return retrieval_score is not None and retrieval_score >= threshold


def grounding_score(answer: str, snippets: list[str], embedder=None) -> float | None:
    """Max similarity between the answer and its cited snippets (S1).

    This is the in-context-mode stand-in for a rerank score: a legitimate
    grounding signal so a faithful, strongly-grounded in-context answer can earn
    HIGH (instead of being capped at MEDIUM by the mere absence of a reranker).
    Uses the local embedder (cosine) when available, else token overlap — both
    offline, no external calls.
    """
    snippets = [s for s in (snippets or []) if s]
    if not answer or not snippets:
        return None
    if embedder is not None:
        try:
            import numpy as np

            vecs = np.asarray(embedder.embed([answer, *snippets]), dtype=float)
            a = vecs[0]
            sims = [float(a @ vecs[i]) for i in range(1, len(vecs))]
            return max(sims) if sims else None
        except Exception:  # noqa: BLE001 - fall back to lexical
            pass
    from ..kb.base import lexical_similarity

    return max(lexical_similarity(answer, s) for s in snippets)


def decide_confidence(
    *,
    source_tier: int | None,
    status: Status,
    faithful: bool,
    retrieval_score: float | None,
    strong_threshold: float = DEFAULT_STRONG_RERANK_SCORE,
) -> Confidence:
    if status != Status.ANSWERED:
        return Confidence.LOW
    if source_tier == 1:
        return Confidence.HIGH
    if faithful and is_strong_retrieval(retrieval_score, strong_threshold):
        return Confidence.HIGH
    return Confidence.MEDIUM


def confidence_rationale(
    *,
    confidence: Confidence,
    source_tier: int | None,
    faithful: bool | None,
    retrieval_score: float | None,
    strong_threshold: float = DEFAULT_STRONG_RERANK_SCORE,
) -> str:
    """A one-line, plain-English explanation of the confidence (Part B audit)."""
    if source_tier == 1:
        return "HIGH — reused a human-approved Answer Library entry (Tier-1, grounded by approval)."
    strong = is_strong_retrieval(retrieval_score, strong_threshold)
    score = "n/a" if retrieval_score is None else f"{retrieval_score:.2f}"
    if confidence == Confidence.HIGH:
        return (f"HIGH — faithfulness passed and grounding is strong "
                f"(score {score} ≥ {strong_threshold}).")
    if confidence == Confidence.MEDIUM:
        why = "grounding signal weak/absent" if not strong else "faithfulness not confirmed"
        return f"MEDIUM — answered and cited, but {why} (score {score})."
    return "LOW — flagged for review (unsupported, unfaithful, ambiguous, conflicting, or unparsed)."
