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
    faithful: bool | None = None
    correctness: float | None = None  # fraction of key_facts covered
    covered_facts: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    n_items: int
    k: int
    recall_at_k: float | None = None      # over items with an expected_source
    faithfulness_rate: float | None = None  # over answered items
    correctness: float | None = None      # mean key-fact coverage over graded items
    coverage: dict = Field(default_factory=dict)  # answered/flagged counts, %s, by_reason
    items: list[EvalItemResult] = Field(default_factory=list)
    note: str = ""
