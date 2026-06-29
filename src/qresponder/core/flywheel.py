"""The flywheel (Scrut L6, §10) — approve reviewed answers into the Library.

Every human-accepted (or edited-then-accepted) answer becomes a versioned
approved Answer Library entry, so Tier-1 coverage compounds and accuracy climbs
with use — independent of the model. Near-identical questions are de-duplicated:
an existing entry is updated and its version bumped rather than appended.

File-safe: writes to a temp file in the same directory then atomically replaces
the target, so existing entries are never lost on a crash.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import yaml

from ..kb.base import lexical_similarity
from ..kb.library import AUTO_REUSE_THRESHOLD, AnswerLibrary, LibraryEntry
from ..kb.tags import normalize_tags
from ..models import AnswerType, QuestionnaireResult, ReviewReason, Status

log = logging.getLogger("qresponder.flywheel")


def _accepted(result: QuestionnaireResult) -> list:
    """Items the reviewer accepted: ANSWERED with a real text answer. This
    naturally includes LIBRARY_CANDIDATE / AMBIGUOUS items the human resolved
    (resolving them means flipping status to ANSWERED with a chosen answer).
    Attachments are excluded — they are files, not reusable Q&A text."""
    out = []
    for r in result.results:
        if r.answer_type == AnswerType.ATTACHMENT:
            continue
        if r.status == Status.ANSWERED and r.answer and r.answer.strip():
            out.append(r)
    return out


def _entries_to_dicts(entries: list[LibraryEntry]) -> list[dict]:
    rows = []
    for e in entries:
        rows.append(
            {
                "question": e.question,
                "answer": e.answer,
                "tags": e.tags,
                "approved_by": e.approved_by,
                "version": e.version,
            }
        )
    return rows


def _safe_write_yaml(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(rows, fh, sort_keys=False, allow_unicode=True, default_flow_style=False)
        os.replace(tmp, path)  # atomic
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _apply_to_library(library: AnswerLibrary, question: str, answer: str, approved_by, tags) -> str:
    """Add or update one entry in an already-loaded library (no I/O).
    Returns 'added' or 'updated'. Dedup at the auto-reuse band."""
    question = question.strip()
    answer = answer.strip()
    existing = None
    for e in library.entries:
        if lexical_similarity(question, e.question) >= AUTO_REUSE_THRESHOLD:
            existing = e
            break
    if existing is not None:
        existing.answer = answer
        existing.version += 1
        if tags:
            existing.tags = sorted(set(existing.tags) | set(tags))
        if approved_by:
            existing.approved_by = approved_by
        return "updated"
    library.entries.append(
        LibraryEntry(question=question, answer=answer, tags=tags, approved_by=approved_by, version=1)
    )
    return "added"


def approve_one(
    question: str,
    answer: str,
    qa_path: str | Path,
    approved_by: str | None = None,
    tags=None,
) -> dict:
    """Approve a single (question, answer) into the Answer Library — the unit
    both the CLI batch approve and the web per-item accept share. Dedup at the
    0.90 band, version-bump on match, atomic write. Returns
    {action, version, total}."""
    library = AnswerLibrary.load(qa_path)  # empty if the file doesn't exist yet
    norm_tags = normalize_tags(tags)
    action = _apply_to_library(library, question, answer, approved_by, norm_tags)
    _safe_write_yaml(Path(qa_path), _entries_to_dicts(library.entries))
    # Report the resulting version of the matching entry.
    version = next(
        (e.version for e in library.entries
         if lexical_similarity(question.strip(), e.question) >= AUTO_REUSE_THRESHOLD),
        1,
    )
    return {"action": action, "version": version, "total": len(library.entries)}


def approve(
    results_path: str | Path,
    qa_path: str | Path,
    approved_by: str | None = None,
    extra_tags=None,
) -> dict:
    """Append accepted answers to the Answer Library, de-duped + versioned.

    Batch path (CLI). Loads once, applies all accepted items, writes once."""
    result = QuestionnaireResult.model_validate_json(
        Path(results_path).read_text(encoding="utf-8")
    )
    library = AnswerLibrary.load(qa_path)
    tags = normalize_tags(extra_tags)

    added = updated = 0
    for r in _accepted(result):
        action = _apply_to_library(library, r.question_text, r.answer, approved_by, tags)
        added += action == "added"
        updated += action == "updated"

    _safe_write_yaml(Path(qa_path), _entries_to_dicts(library.entries))
    log.info("Flywheel: %d added, %d updated; library now %d entries", added, updated, len(library.entries))
    return {"added": added, "updated": updated, "total": len(library.entries)}
