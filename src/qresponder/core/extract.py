"""Question extraction — LLM call #1 (§6 Stage B, §14).

The model (not code) decides what is a question vs. an instruction/header and
what answer type each expects, reading the layout-aware IR. Output is validated
to `Question` objects with a defensive parse + one retry.
"""

from __future__ import annotations

import logging

from ..ingest.ir import Document
from ..llm.base import LLMProvider
from ..llm import prompts
from ..models import AnswerType, Question
from .parsing import parse_json_array

log = logging.getLogger("qresponder.extract")

_VALID_TYPES = {t.value for t in AnswerType}


def _coerce_question(raw: dict, index: int) -> Question | None:
    text = (raw.get("question_text") or raw.get("text") or "").strip()
    if not text:
        return None
    atype = str(raw.get("answer_type", "unknown")).strip().lower()
    if atype not in _VALID_TYPES:
        atype = "unknown"
    interps = raw.get("interpretations") or []
    if not isinstance(interps, list):
        interps = []
    return Question(
        id=str(raw.get("id") or f"q{index}"),
        text=text,
        answer_type=AnswerType(atype),
        section=(raw.get("section") or None),
        location_hint=(raw.get("location_hint") or None),
        answer_location_hint=(raw.get("answer_location_hint") or None),
        ambiguous=bool(raw.get("ambiguous", False)),
        interpretations=[str(x) for x in interps],
    )


def extract_questions(doc: Document, provider: LLMProvider) -> list[Question]:
    """Extract questions from a document's IR. Retries the call once on parse failure."""
    layout_ir = doc.render_markdown()
    system = prompts.EXTRACT_SYSTEM
    user = prompts.build_extract_user(layout_ir)

    raw_items: list | None = None
    last_err: Exception | None = None
    for attempt in range(2):
        # Generous budget: a large questionnaire yields a long JSON array, and some
        # providers (e.g. Gemini's "thinking") consume part of the output budget
        # before the JSON — too small a limit truncates the array mid-object.
        text = provider.complete(system, user, max_tokens=8192)
        try:
            raw_items = parse_json_array(text)
            break
        except ValueError as exc:
            last_err = exc
            log.warning("Extraction parse failed (attempt %d): %s", attempt + 1, exc)

    if raw_items is None:
        raise ValueError(
            f"Failed to extract questions after 2 attempts: {last_err}"
        )

    questions: list[Question] = []
    for i, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            continue
        q = _coerce_question(raw, i)
        if q is not None:
            questions.append(q)

    # GUARDRAIL (F3): results are keyed by question id downstream; a model that
    # emits duplicate ids would silently drop questions. Make ids unique,
    # preserving model ids where unique and suffixing on collision.
    seen: set[str] = set()
    for q in questions:
        base = q.id
        n = 2
        while q.id in seen:
            q.id = f"{base}-{n}"
            n += 1
        seen.add(q.id)

    log.info("Extracted %d question(s)", len(questions))
    return questions
