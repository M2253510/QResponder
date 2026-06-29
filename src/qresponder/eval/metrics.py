"""Eval report models (§11)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvalItemResult(BaseModel):
    question: str
    status: str
    review_reason: str
    confidence: str
    source_tier: int | None = None
    answer: str = ""
    recall_hit: bool | None = None  # None = N/A (no expected_source / not retrieval)
    recall_rank: int | None = None  # 1-based rank of expected_source in retrieval
    faithful: bool | None = None
    correctness: float | None = None  # fraction of key_facts covered
    answer_relevancy: float | None = None   # RAGAS-aligned (question<->answer)
    context_precision: float | None = None  # cited sources that are the expected one
    context_recall: float | None = None     # key-facts present in cited context
    grounding_score: float | None = None  # rerank (retrieval) or grounding (in-context)
    covered_facts: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    n_items: int
    k: int
    # RAGAS-aligned set (faithfulness + correctness are judge-based; the rest are
    # deterministic offline proxies — lexical/grounding — so CI stays stable).
    faithfulness_rate: float | None = None  # answers grounded in cited context
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    correctness: float | None = None        # mean key-fact coverage over graded items
    # Retrieval quality.
    recall_at_k: float | None = None
    mrr: float | None = None
    coverage: dict = Field(default_factory=dict)  # answered/flagged counts, %s, by_reason
    # Abstention is a first-class metric — restraint is the product, not a failure.
    abstention: dict = Field(default_factory=dict)  # {rate, by_reason}
    # Calibration: measured correctness per predicted-confidence bucket.
    calibration: dict = Field(default_factory=dict)  # {high|medium|low: {n, correctness}}
    score_distribution: dict = Field(default_factory=dict)
    suggested_threshold: float | None = None
    items: list[EvalItemResult] = Field(default_factory=list)
    note: str = ""
