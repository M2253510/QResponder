"""Answer-type enforcement (Phase 7, Part D).

Format-shaping ONLY. We shape an already-grounded answer to fit its answer_type
(select/dropdown → an allowed option; yes/no left as the model grounded it). This
can NEVER weaken grounding: it only runs on ANSWERED results, never fabricates a
"Yes"/option the answer didn't support, and never flips a NEEDS_REVIEW (unsupported)
item into an answer. If a grounded answer can't be mapped to an allowed option, it
is left as-is for the human rather than forced.
"""

from __future__ import annotations

import re

from ..models import AnswerResult, AnswerType, Status


def coerce_to_options(value: str, options: list[str]) -> str | None:
    """Map a free-text answer to one of `options` if it clearly corresponds, else
    None. Same logic the Excel dropdown write-back uses (Part F)."""
    if not value or not options:
        return None
    vlow = value.strip().lower()
    for opt in options:
        if vlow == opt.lower():
            return opt
    for opt in options:
        if vlow.startswith(opt.lower()):
            return opt
    for opt in options:
        # Word-boundary match so a short option ("No") doesn't match inside a longer
        # word ("know" / "not") — only as a standalone token.
        if re.search(rf"\b{re.escape(opt.lower())}\b", vlow):
            return opt
    return None


def shape_to_type(result: AnswerResult, allowed_options: list[str] | None = None) -> AnswerResult:
    """Shape an ANSWERED result's text to its answer_type. No-op on NEEDS_REVIEW
    (we never turn an abstention into a forced answer)."""
    if result.status != Status.ANSWERED or not result.answer:
        return result
    if allowed_options and result.answer_type in (AnswerType.MULTI_SELECT, AnswerType.YES_NO):
        mapped = coerce_to_options(result.answer, allowed_options)
        if mapped is not None:
            result.answer = mapped
        # else: leave the grounded answer untouched — never force an option.
    return result
