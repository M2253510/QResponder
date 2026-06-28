"""MockProvider — deterministic, network-free responses for tests (§12).

Two modes:
  * Scripted: pass `responses=[...]`; each `complete()` pops the next string.
  * Smart default: when no scripted response remains, it inspects the prompt and
    produces realistic structured JSON — heuristic question extraction for the
    extraction call, and keyword-grounded answers for the answering call. The
    grounding heuristic intentionally returns NEEDS_REVIEW when the KB context
    does not support a question, so the no-fabrication guardrail is exercised.
"""

from __future__ import annotations

import json
import re

from . import prompts

_STOPWORDS = {
    "do", "does", "is", "are", "have", "has", "the", "you", "your", "for",
    "and", "with", "what", "please", "provide", "attach", "of", "a", "an",
    "to", "in", "on", "we", "our", "most", "recent", "report",
    # Generic domain words that would otherwise create false "grounding" — the
    # mock should match on distinctive terms (e.g. "encryption", "incident"),
    # not on filler that appears in nearly every policy document.
    "data", "customer", "customers", "company", "system", "systems",
    "information", "service", "services", "use", "used", "all", "any",
}


def _answer_type(text: str) -> str:
    t = text.lower().strip()
    if any(w in t for w in ("attach", "upload", "provide your", "provide a copy")):
        return "attachment"
    if t.startswith(("do ", "does ", "is ", "are ", "have ", "has ", "can ", "will ", "did ")):
        return "yes_no"
    if t.startswith(("select", "which of", "choose")):
        return "multi_select"
    return "text"


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9\-]+", text.lower())
    return {w for w in words if len(w) > 3 and w not in _STOPWORDS}


class MockProvider:
    def __init__(self, responses: list[str] | None = None):
        self._responses = list(responses or [])
        self.calls: list[tuple[str, str]] = []  # (system, user) for assertions

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        self.calls.append((system, user))
        if self._responses:
            return self._responses.pop(0)

        if system.startswith("You extract questions"):
            return self._mock_extract(user)
        if system.startswith("Answer STRICTLY"):
            return self._mock_answer(user)
        if system.startswith("You are a strict faithfulness verifier"):
            return self._mock_faithfulness(user)
        if system.startswith("You are grading answers"):
            return self._mock_correctness(user)
        # doctor preflight uses a tiny JSON echo request.
        return '{"ok": true}'

    # --- eval correctness ------------------------------------------------
    def _mock_correctness(self, user: str) -> str:
        """A key fact is 'covered' if its content words appear in the answer."""
        try:
            start, end = user.find("["), user.rfind("]")
            items = json.loads(user[start : end + 1]) if start != -1 else []
        except (json.JSONDecodeError, ValueError):
            items = []
        out = []
        for it in items:
            answer_lower = str(it.get("answer", "")).lower()
            covered, missing = [], []
            for fact in it.get("key_facts") or []:
                fact_words = _content_words(str(fact))
                if fact_words and all(w in answer_lower for w in fact_words):
                    covered.append(fact)
                else:
                    missing.append(fact)
            out.append({"id": str(it.get("id", "")), "covered_facts": covered, "missing_facts": missing})
        return json.dumps(out)

    # --- faithfulness ----------------------------------------------------
    def _mock_faithfulness(self, user: str) -> str:
        """Honest verdict for the mock's own answers: a claim is faithful when
        its words actually appear in the cited snippets (which the mock answerer
        guarantees, since it answers WITH a snippet). This keeps the mock truthful
        rather than rubber-stamping."""
        items = []
        try:
            start, end = user.find("["), user.rfind("]")
            items = json.loads(user[start : end + 1]) if start != -1 else []
        except (json.JSONDecodeError, ValueError):
            items = []
        out = []
        for it in items:
            answer = str(it.get("answer", ""))
            snippets = " ".join(str(s) for s in (it.get("snippets") or []))
            snip_lower = snippets.lower()
            claim_words = _content_words(answer)
            # Faithful if the answer's content words are largely present in the
            # cited snippets (entailment proxy).
            if not claim_words:
                faithful = bool(snippets)
            else:
                present = sum(1 for w in claim_words if w in snip_lower)
                faithful = present / len(claim_words) >= 0.5
            out.append(
                {
                    "id": str(it.get("id", "")),
                    "faithful": faithful,
                    "unsupported_claims": [] if faithful else ["answer not entailed by snippets"],
                }
            )
        return json.dumps(out)

    # --- extraction ------------------------------------------------------
    def _mock_extract(self, user: str) -> str:
        items: list[dict] = []
        current_section: str | None = None
        n = 0
        for raw in user.splitlines():
            line = raw.strip()
            if not line.startswith("- ") or " = " not in line:
                continue
            location, _, rest = line[2:].partition(" = ")
            location = location.strip()
            # Split off trailing "  [flags]".
            flags = ""
            m = re.search(r"\s\[([^\]]+)\]\s*$", rest)
            if m:
                flags = m.group(1)
                rest = rest[: m.start()].strip()
            text = rest.strip()
            is_question = text.endswith("?") or _answer_type(text) == "attachment"
            is_section = ("section" in flags or "merged" in flags) and not is_question
            if is_section:
                current_section = text
                continue
            if not is_question:
                continue
            n += 1
            items.append(
                {
                    "id": f"q{n}",
                    "question_text": text,
                    "answer_type": _answer_type(text),
                    "section": current_section,
                    "location_hint": location,
                    "ambiguous": False,
                    "interpretations": [],
                }
            )
        return json.dumps(items)

    # --- answering -------------------------------------------------------
    def _mock_answer(self, user: str) -> str:
        kb_context, questions = self._split_answer_user(user)
        kb_lower = kb_context.lower()
        kb_lines = [ln.strip() for ln in kb_context.splitlines() if ln.strip()]
        out: list[dict] = []
        for q in questions:
            qtext = q.get("question_text", "")
            atype = q.get("answer_type", "text")
            qid = q.get("question_id") or q.get("id")
            if atype == "attachment":
                # Phase 0 does not resolve attachments; flag for review.
                out.append(
                    {
                        "question_id": qid,
                        "answer": "",
                        "answer_type": atype,
                        "citations": [],
                        "confidence": "low",
                        "status": "needs_review",
                        "review_reason": "attachment_unresolved",
                        "missing_info": "Attachment request — resolve the document manually (Phase 2).",
                        "source_tier": None,
                    }
                )
                continue

            words = _content_words(qtext)
            overlap = {w for w in words if w in kb_lower}
            if len(overlap) >= 2:
                snippet = next(
                    (ln for ln in kb_lines if any(w in ln.lower() for w in overlap)),
                    kb_lines[0] if kb_lines else "",
                )
                out.append(
                    {
                        "question_id": qid,
                        "answer": f"Based on the knowledge base: {snippet}",
                        "answer_type": atype,
                        "citations": [{"source": "knowledge-base", "snippet": snippet}],
                        "confidence": "medium",
                        "status": "answered",
                        "review_reason": "none",
                        "missing_info": None,
                        "source_tier": 2,
                    }
                )
            else:
                out.append(
                    {
                        "question_id": qid,
                        "answer": "",
                        "answer_type": atype,
                        "citations": [],
                        "confidence": "low",
                        "status": "needs_review",
                        "review_reason": "unsupported",
                        "missing_info": f"No knowledge base content supports: '{qtext}'.",
                        "source_tier": None,
                    }
                )
        return json.dumps(out)

    @staticmethod
    def _split_answer_user(user: str) -> tuple[str, list[dict]]:
        kb_context = ""
        questions: list[dict] = []
        if prompts.QUESTIONS_MARKER in user:
            ctx_part, _, q_part = user.partition(prompts.QUESTIONS_MARKER)
            kb_context = ctx_part.replace(prompts.KB_CONTEXT_MARKER, "").strip()
            try:
                questions = json.loads(q_part.strip())
            except json.JSONDecodeError:
                questions = []
        return kb_context, questions
