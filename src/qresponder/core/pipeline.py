"""End-to-end pipeline: file -> IR -> extract -> orchestrate -> results (§16.8).

This is the single entry point the CLI and tests call. It wires the layers but
holds no answering logic of its own — that lives in extract/orchestrate/answer.
"""

from __future__ import annotations

import logging

from ..config import Config
from ..ingest.base import load_document
from ..kb.in_context import InContextKB
from ..kb.library import AnswerLibrary
from ..llm.base import LLMProvider, make_provider
from ..models import AnswerResult, AnswerType, Question, QuestionnaireResult, Status
from .extract import extract_questions
from .orchestrate import orchestrate

log = logging.getLogger("qresponder.pipeline")


def _load_kb(kb_dir, qa_path, evidence_dir, config):
    """Build (library, kb, evidence) the one way both the questionnaire and Ask
    paths use — so Ask reuses the exact grounded path, not a parallel one."""
    library = AnswerLibrary.load(qa_path)
    if config.kb_mode == "retrieval":
        from ..kb.retrieval import RetrievalKB

        kb = RetrievalKB.load(kb_dir, config)
    else:
        kb = InContextKB.load(kb_dir)
    evidence = None
    ev_dir = evidence_dir or config.evidence_dir
    if ev_dir:
        from ..kb.evidence import EvidenceIndex

        evidence = EvidenceIndex.load(ev_dir)
    return library, kb, evidence


def run_ask(
    question_text: str,
    kb_dir: str | None,
    qa_path: str | None,
    config: Config,
    scope_tags=None,
    provider: LLMProvider | None = None,
    evidence_dir: str | None = None,
    history=None,
    preset: str | None = None,
    style: str | None = None,
) -> AnswerResult:
    """Answer ONE natural-language question through the EXACT same grounded path
    as a questionnaire (library -> retrieval -> grounded generation -> faithfulness
    -> conflict -> abstain). Same AuditTrail semantics; no lighter path."""
    provider = provider or make_provider(config)
    library, kb, evidence = _load_kb(kb_dir, qa_path, evidence_dir, config)
    q = Question(id="ask", text=question_text.strip(), answer_type=AnswerType.TEXT)
    results = orchestrate(
        [q], provider, library, kb, config,
        scope_tags=scope_tags, evidence=evidence, history=history,
        preset=preset, style=style,
    )
    return results[0]


def run_pipeline(
    questionnaire_path: str,
    kb_dir: str | None,
    qa_path: str | None,
    config: Config,
    scope_tags=None,
    provider: LLMProvider | None = None,
    evidence_dir: str | None = None,
    history=None,
    preset: str | None = None,
    style: str | None = None,
    on_event=None,
) -> QuestionnaireResult:
    import os

    provider = provider or make_provider(config)
    fname = os.path.basename(str(questionnaire_path))

    def E(etype, **data):
        if on_event:
            on_event({"type": etype, "file": fname, **data})

    try:
        E("file_started", file=fname)
        log.info("Ingesting %s", questionnaire_path)
        doc = load_document(questionnaire_path)

        log.info("Extracting questions")
        questions = extract_questions(doc, provider)
        E("parsed", questions=len(questions))

        library, kb, evidence = _load_kb(kb_dir, qa_path, evidence_dir, config)

        log.info("Answering %d question(s)", len(questions))
        results = orchestrate(
            questions, provider, library, kb, config,
            scope_tags=scope_tags, evidence=evidence, history=history,
            preset=preset, style=style, on_event=on_event,
        )
    except Exception as exc:  # noqa: BLE001 - surface as an event, then re-raise
        E("error", error=str(exc))
        raise

    answered = sum(1 for r in results if r.status == Status.ANSWERED)
    flagged = len(results) - answered
    log.info("Done: %d answered, %d flagged for review", answered, flagged)
    E("file_done", answered=answered, flagged=flagged)
    return QuestionnaireResult(source_file=str(questionnaire_path), results=results)
