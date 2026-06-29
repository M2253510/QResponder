"""Core data models for QRESPONDER (pydantic v2).

These mirror §13.1 of the build spec exactly. They are the contract between the
extraction, retrieval, answering, and output layers — keep them stable, and
design new fields so later phases (faithfulness, attachments, write-back) slot
in without breaking callers.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class AnswerType(str, Enum):
    TEXT = "text"
    YES_NO = "yes_no"
    MULTI_SELECT = "multi_select"
    ATTACHMENT = "attachment"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Status(str, Enum):
    ANSWERED = "answered"
    NEEDS_REVIEW = "needs_review"


class ReviewReason(str, Enum):
    NONE = "none"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"
    FAITHFULNESS_FAIL = "faithfulness_fail"
    PARSE_ERROR = "parse_error"
    ATTACHMENT_UNRESOLVED = "attachment_unresolved"
    LIBRARY_CANDIDATE = "library_candidate"
    CONFLICT = "conflict"
    INJECTION_SUSPECTED = "injection_suspected"  # Part C
    HISTORY_CONFLICT = "history_conflict"        # Part G1


class Question(BaseModel):
    id: str
    text: str
    answer_type: AnswerType = AnswerType.UNKNOWN
    section: str | None = None
    location_hint: str | None = None  # "Sheet1!C7" — the QUESTION's cell anchor
    # Where the ANSWER goes (distinct from location_hint), for write-back (C3).
    answer_location_hint: str | None = None
    # Populated when ambiguous; the extractor proposes readings, the human picks.
    interpretations: list[str] = Field(default_factory=list)
    ambiguous: bool = False


class Citation(BaseModel):
    source: str
    snippet: str
    # Set by the faithfulness check (Phase 1+); None means "not yet verified".
    faithful: bool | None = None


class InterpretationOption(BaseModel):
    """One reading of an ambiguous question with its own grounded draft (C1, §8)."""

    interpretation: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    status: Status = Status.NEEDS_REVIEW


class SubAnswer(BaseModel):
    """One part of a decomposed compound question (Part G2)."""

    part: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    status: Status = Status.NEEDS_REVIEW


class RetrievedCandidate(BaseModel):
    source: str
    snippet: str
    score: float | None = None


class HumanAction(BaseModel):
    type: str = "none"  # accepted | edited | picked | attached | none
    by: str | None = None
    at: str | None = None
    original_answer: str | None = None


class AuditTrail(BaseModel):
    """Persisted evidence chain for one answer (Part B): what was retrieved, what
    was cited, the faithfulness verdict, why the confidence, and the human action."""

    retrieved: list[RetrievedCandidate] = Field(default_factory=list)
    cited: list[Citation] = Field(default_factory=list)
    faithfulness: dict = Field(default_factory=dict)  # {passed: bool, reason: str}
    confidence_rationale: str = ""
    human_action: HumanAction = Field(default_factory=HumanAction)


class AnswerResult(BaseModel):
    question_id: str
    question_text: str
    answer: str
    answer_type: AnswerType
    citations: list[Citation] = Field(default_factory=list)
    confidence: Confidence
    status: Status
    review_reason: ReviewReason = ReviewReason.NONE
    missing_info: str | None = None
    attachment_path: str | None = None  # resolved attachment, if any (C2)
    attachment_candidates: list[str] = Field(default_factory=list)  # unresolved options (C2)
    source_tier: int | None = None  # 1=Library .. 3=Evidence
    # Interpretation candidates for ambiguous questions (C1).
    candidates: list[InterpretationOption] = Field(default_factory=list)
    # Explainable grounding/rerank score behind the confidence decision (S1/S2).
    grounding_score: float | None = None
    # Write-back anchors carried from the Question for format-perfect output (C3).
    location_hint: str | None = None
    answer_location_hint: str | None = None
    # Set when this answer contradicts another source (D1): a short description
    # of the conflicting source/answer the human must reconcile.
    conflict_with: str | None = None
    # Persisted audit/evidence chain (Part B).
    audit: AuditTrail | None = None
    # SME routing owner for flagged items (Part E).
    owner: str | None = None
    # Duplicate-group link: questions answered once and applied to all (Part E).
    group_id: str | None = None
    # Decomposed compound-question parts (Part G2).
    subanswers: list[SubAnswer] = Field(default_factory=list)


class QuestionnaireResult(BaseModel):
    source_file: str
    results: list[AnswerResult] = Field(default_factory=list)
