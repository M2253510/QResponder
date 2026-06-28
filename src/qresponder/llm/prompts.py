"""The two core prompts (§14) plus the markers the MockProvider keys off.

Keeping the structure explicit (clear section markers) makes model-agnostic
strict-JSON parsing reliable on weak local models, and lets the deterministic
MockProvider locate the KB context and questions without a real model.
"""

from __future__ import annotations

import json

# Markers embedded in the answer-call user message. Stable strings so the
# defensive parser and MockProvider can split context from questions.
KB_CONTEXT_MARKER = "=== KNOWLEDGE BASE CONTEXT ==="
QUESTIONS_MARKER = "=== QUESTIONS (JSON) ==="

# --- Call #1: extraction -----------------------------------------------------

EXTRACT_SYSTEM = (
    "You extract questions from vendor security questionnaires. Use the layout "
    "(cell coordinates, merges, colors, sections) to decide what is a question "
    "vs. an instruction or header, and what answer each expects. "
    "Return ONLY a JSON array, no prose, no code fences. "
    "Each item must be an object with keys: "
    "id (string), question_text (string), "
    "answer_type (one of: text, yes_no, multi_select, attachment), "
    "section (string or null), location_hint (string or null), "
    "ambiguous (boolean), interpretations (array of strings)."
)


def build_extract_user(layout_ir: str) -> str:
    return (
        "Here is the layout-aware representation of the questionnaire. Extract "
        "every question.\n\n" + layout_ir
    )


# --- Call #2: answering ------------------------------------------------------

ANSWER_SYSTEM = (
    "Answer STRICTLY from the provided knowledge base context. Prefer APPROVED "
    "ANSWERS when a question matches. Never invent certifications, controls, "
    "audits, or compliance status. If the knowledge base does not support an "
    "answer, set status to 'needs_review', set review_reason to 'unsupported', "
    "and state what is missing in missing_info. Constrain each answer to its "
    "answer_type. Every 'answered' item MUST include at least one citation with "
    "the exact source and a verbatim snippet from the context. "
    "Return ONLY a JSON array, no prose, no code fences. Each item must be an "
    "object with keys: question_id, answer, answer_type, "
    "citations (array of {source, snippet}), confidence (high|medium|low), "
    "status (answered|needs_review), "
    "review_reason (none|ambiguous|unsupported|faithfulness_fail|parse_error|attachment_unresolved), "
    "missing_info (string or null), source_tier (integer or null)."
)


def build_answer_user(kb_context: str, questions: list[dict]) -> str:
    return (
        f"{KB_CONTEXT_MARKER}\n{kb_context}\n\n"
        f"{QUESTIONS_MARKER}\n{json.dumps(questions, ensure_ascii=False, indent=2)}"
    )


# --- Faithfulness / citation verification (Phase 1, §11) ---------------------

FAITHFULNESS_SYSTEM = (
    "You are a strict faithfulness verifier for compliance answers. For each "
    "item you are given an answer and the snippets it cites. Decide whether "
    "EVERY factual claim in the answer is directly entailed by the cited "
    "snippets — not merely topically related. If any claim is not supported by "
    "the snippets, the item is NOT faithful. Be conservative: when in doubt, "
    "mark it not faithful. Return ONLY a JSON array, no prose, no code fences. "
    "Each item: {id (string), faithful (boolean), unsupported_claims (array of strings)}."
)


def build_faithfulness_user(items: list[dict]) -> str:
    """items: [{id, answer, snippets: [str, ...]}]."""
    return (
        "Verify each item's faithfulness against its cited snippets.\n\n"
        + json.dumps(items, ensure_ascii=False, indent=2)
    )


# --- Eval correctness judge (Phase 1, §11) -----------------------------------

EVAL_CORRECTNESS_SYSTEM = (
    "You are grading answers against a list of expected key facts. For each "
    "item, decide which key facts are covered by the answer and which are "
    "missing. A fact is covered only if the answer actually states it. "
    "Return ONLY a JSON array, no prose, no code fences. Each item: "
    "{id (string), covered_facts (array of strings), missing_facts (array of strings)}."
)


def build_eval_correctness_user(items: list[dict]) -> str:
    """items: [{id, answer, key_facts: [str, ...]}]."""
    return (
        "Grade each answer's coverage of its key facts.\n\n"
        + json.dumps(items, ensure_ascii=False, indent=2)
    )
