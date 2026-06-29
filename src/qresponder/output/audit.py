"""Audit / evidence pack export (Part B).

Turns a completed questionnaire into audit-ready evidence: per question, the full
chain question → retrieved candidates → cited snippets → faithfulness verdict →
confidence rationale → human action. Emits audit.json (machine) and audit.md
(human-readable), and can bundle the answered files + audit into a single zip.

This is serialization of the AuditTrail the engine already captured — no logic.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ..models import QuestionnaireResult, Status


def _audit_record(r) -> dict:
    a = r.audit
    return {
        "question_id": r.question_id,
        "question": r.question_text,
        "answer": r.answer,
        "status": r.status.value,
        "confidence": r.confidence.value,
        "review_reason": r.review_reason.value,
        "source_tier": r.source_tier,
        "audit": a.model_dump() if a is not None else None,
    }


def build_audit_json(result: QuestionnaireResult) -> dict:
    return {
        "source_file": result.source_file,
        "n_items": len(result.results),
        "items": [_audit_record(r) for r in result.results],
    }


def build_audit_md(result: QuestionnaireResult) -> str:
    lines = [f"# Evidence pack — {Path(result.source_file).name}", ""]
    answered = sum(1 for r in result.results if r.status == Status.ANSWERED)
    lines.append(f"**{len(result.results)}** questions · **{answered}** answered · "
                 f"**{len(result.results) - answered}** flagged for review")
    lines.append("")
    lines.append("> Each answer below shows its full evidence chain: what was "
                 "retrieved, what was cited, the faithfulness verdict, why this "
                 "confidence, and the human action taken.")
    lines.append("")
    for i, r in enumerate(result.results, 1):
        a = r.audit
        lines.append(f"## {i}. {r.question_text}")
        lines.append("")
        lines.append(f"- **Answer:** {r.answer or '(none — flagged for review)'}")
        lines.append(f"- **Status:** {r.status.value} · **Confidence:** {r.confidence.value}"
                     + (f" · **Reason:** {r.review_reason.value}" if r.review_reason.value != "none" else ""))
        if a is not None:
            if a.retrieved:
                lines.append("- **Retrieved (considered):**")
                for c in a.retrieved:
                    sc = "" if c.score is None else f" _(score {c.score})_"
                    lines.append(f"    - `{c.source}`{sc}: {_short(c.snippet)}")
            if a.cited:
                lines.append("- **Cited:**")
                for c in a.cited:
                    mark = "✓" if c.faithful is True else "✗" if c.faithful is False else "·"
                    lines.append(f"    - {mark} `{c.source}`: {_short(c.snippet)}")
            fa = a.faithfulness or {}
            lines.append(f"- **Faithfulness:** {'PASSED' if fa.get('passed') else 'not confirmed'}"
                         + (f" — {fa.get('reason')}" if fa.get("reason") else ""))
            if a.confidence_rationale:
                lines.append(f"- **Confidence rationale:** {a.confidence_rationale}")
            ha = a.human_action
            if ha and ha.type and ha.type != "none":
                who = f" by {ha.by}" if ha.by else ""
                when = f" at {ha.at}" if ha.at else ""
                extra = ""
                if ha.original_answer and ha.type == "edited":
                    extra = f" (original: {_short(ha.original_answer)})"
                lines.append(f"- **Human action:** {ha.type}{who}{when}{extra}")
            else:
                lines.append("- **Human action:** none (draft — not yet reviewed)")
        lines.append("")
    return "\n".join(lines)


def _short(text: str, n: int = 200) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def write_audit(result: QuestionnaireResult, out_dir: str | Path) -> dict:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "audit.json").write_text(json.dumps(build_audit_json(result), indent=2), encoding="utf-8")
    (d / "audit.md").write_text(build_audit_md(result), encoding="utf-8")
    return {"json": str(d / "audit.json"), "md": str(d / "audit.md")}


def bundle_zip(run_dir: str | Path, zip_name: str = "evidence_pack.zip") -> str:
    """Bundle all output artifacts in a run dir (answered.*, results.json,
    review.md, audit.*) into one zip."""
    d = Path(run_dir)
    zip_path = d / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in sorted(d.iterdir()):
            if fp.is_file() and fp.name != zip_name:
                zf.write(fp, fp.name)
    return str(zip_path)
