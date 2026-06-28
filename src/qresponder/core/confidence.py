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
