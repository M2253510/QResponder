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
from ..models import QuestionnaireResult, Status
from .extract import extract_questions
from .orchestrate import orchestrate

log = logging.getLogger("qresponder.pipeline")


def run_pipeline(
    questionnaire_path: str,
    kb_dir: str | None,
    qa_path: str | None,
    config: Config,
    scope_tags=None,
    provider: LLMProvider | None = None,
) -> QuestionnaireResult:
    provider = provider or make_provider(config)

    log.info("Ingesting %s", questionnaire_path)
    doc = load_document(questionnaire_path)

    log.info("Extracting questions")
    questions = extract_questions(doc, provider)

    library = AnswerLibrary.load(qa_path)
    if config.kb_mode == "retrieval":
        from ..kb.retrieval import RetrievalKB

        kb = RetrievalKB.load(kb_dir, config)
    else:
        kb = InContextKB.load(kb_dir)

    log.info("Answering %d question(s)", len(questions))
    results = orchestrate(questions, provider, library, kb, config, scope_tags=scope_tags)

    answered = sum(1 for r in results if r.status == Status.ANSWERED)
    flagged = len(results) - answered
    log.info("Done: %d answered, %d flagged for review", answered, flagged)

    return QuestionnaireResult(source_file=str(questionnaire_path), results=results)
