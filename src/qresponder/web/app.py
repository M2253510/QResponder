"""FastAPI backend for the local web review UI (Phase 4, E2).

Thin orchestration over the engine. Holds an in-memory job registry keyed by
run_id; each run has its own output dir. Every accept/edit flows back through the
flywheel (`approve_one`) so using the UI trains the Answer Library — an
edited-then-accepted answer trains on the edited text. Binds 127.0.0.1 by
default; no auth; keys never reach the browser.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import Config, load_config
from ..core.flywheel import approve_one
from ..core.pipeline import run_pipeline
from ..kb.tags import parse_tags
from ..models import AnswerType, QuestionnaireResult, ReviewReason, Status
from ..output.writer import write_all
from ..output.writeback import has_answer_anchors, write_back

log = logging.getLogger("qresponder.web")

_STATIC_DIR = Path(__file__).parent / "static"


class _Job:
    def __init__(self, run_id: str, out_dir: Path, qa_path: str, tags: list[str]):
        self.run_id = run_id
        self.out_dir = out_dir
        self.qa_path = qa_path
        self.tags = tags
        self.status = "pending"  # pending | running | done | error
        self.error: str | None = None
        self.questionnaire_path: str | None = None
        self.result: QuestionnaireResult | None = None
        # qid -> approved answer text, for idempotent re-accept (no spurious bumps).
        self.approved: dict[str, str] = {}


class AcceptBody(BaseModel):
    answer: str | None = None
    interpretation: str | None = None
    attachment: str | None = None
    approved_by: str | None = "web"


def _summary(result: QuestionnaireResult) -> dict:
    total = len(result.results)
    answered = [r for r in result.results if r.status == Status.ANSWERED]
    high = sum(1 for r in answered if r.confidence.value == "high")
    flagged = [r for r in result.results if r.status == Status.NEEDS_REVIEW]
    by_reason = Counter(r.review_reason.value for r in flagged)
    return {
        "total": total,
        "answered": len(answered),
        "auto_answered_high": high,
        "flagged": len(flagged),
        "flagged_by_reason": dict(by_reason),
    }


def _persist(job: _Job) -> None:
    if job.result is not None:
        job.out_dir.mkdir(parents=True, exist_ok=True)
        (job.out_dir / "results.json").write_text(
            job.result.model_dump_json(indent=2), encoding="utf-8"
        )


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(title="QRESPONDER review UI")
    jobs: dict[str, _Job] = {}
    app.state.jobs = jobs  # test seam: inspect/inject runs

    def _run(job: _Job, kb, evidence, qa, mode):
        job.status = "running"
        try:
            cfg = config.model_copy()
            if mode:
                cfg.kb_mode = mode
            result = run_pipeline(
                job.questionnaire_path, kb, qa, cfg,
                scope_tags=job.tags, evidence_dir=evidence,
            )
            job.result = result
            _persist(job)
            job.status = "done"
        except Exception as exc:  # noqa: BLE001
            log.exception("Run %s failed", job.run_id)
            job.error = str(exc)
            job.status = "error"

    @app.get("/api/status")
    def status():
        provider = config.llm_provider
        model = config.anthropic_model if provider == "anthropic" else config.llm_model
        # Never expose the key — only the active provider/model name.
        return {"provider": provider, "model": model, "kb_mode": config.kb_mode}

    @app.post("/api/runs")
    async def create_run(
        questionnaire: UploadFile,
        kb: str = Form(None),
        evidence: str = Form(None),
        qa: str = Form(None),
        tags: str = Form(None),
        mode: str = Form(None),
    ):
        run_id = uuid.uuid4().hex[:12]
        out_dir = Path(config.extra.get("web_runs_dir", "web_runs")) / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        qa_path = qa or str(out_dir / "qa.yaml")
        job = _Job(run_id, out_dir, qa_path, parse_tags(tags))

        dest = out_dir / (Path(questionnaire.filename or "questionnaire").name)
        dest.write_bytes(await questionnaire.read())
        job.questionnaire_path = str(dest)
        jobs[run_id] = job

        threading.Thread(target=_run, args=(job, kb, evidence, qa_path, mode), daemon=True).start()
        return {"run_id": run_id}

    def _get(run_id: str) -> _Job:
        job = jobs.get(run_id)
        if job is None:
            raise HTTPException(status_code=404, detail="run not found")
        return job

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        job = _get(run_id)
        payload = {"status": job.status, "error": job.error}
        if job.result is not None:
            payload["summary"] = _summary(job.result)
            payload["results"] = [r.model_dump() for r in job.result.results]
            payload["approved"] = list(job.approved.keys())
        return payload

    @app.post("/api/runs/{run_id}/items/{qid}/accept")
    def accept(run_id: str, qid: str, body: AcceptBody):
        job = _get(run_id)
        if job.result is None:
            raise HTTPException(status_code=409, detail="run not finished")
        item = next((r for r in job.result.results if r.question_id == qid), None)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")

        is_attachment = item.answer_type == AnswerType.ATTACHMENT or body.attachment
        if body.attachment:
            # Confirm/pick an evidence file: store filename; files aren't Q&A text.
            item.attachment_path = body.attachment
            item.answer = body.attachment
            item.answer_type = AnswerType.ATTACHMENT
            final_answer = body.attachment
        elif body.interpretation:
            chosen = next((c for c in item.candidates if c.interpretation == body.interpretation), None)
            final_answer = (body.answer or (chosen.answer if chosen else "")).strip()
            if chosen is not None:
                item.citations = chosen.citations
            item.answer = final_answer
        else:
            final_answer = (body.answer if body.answer is not None else item.answer).strip()
            item.answer = final_answer

        item.status = Status.ANSWERED
        item.review_reason = ReviewReason.NONE
        item.conflict_with = None
        if not is_attachment:
            from ..models import Confidence

            item.confidence = Confidence.HIGH  # human-approved is the highest authority
        _persist(job)

        # Flywheel: train on the FINAL (possibly edited) text. Idempotent per run.
        trained = False
        library = None
        if not is_attachment and final_answer:
            if job.approved.get(qid) != final_answer:
                library = approve_one(
                    item.question_text, final_answer, job.qa_path,
                    approved_by=body.approved_by, tags=job.tags,
                )
                job.approved[qid] = final_answer
                trained = True
            else:
                trained = True  # already trained with this exact text

        return {"item": item.model_dump(), "trained": trained, "library": library}

    @app.post("/api/runs/{run_id}/export")
    def export(run_id: str):
        job = _get(run_id)
        if job.result is None:
            raise HTTPException(status_code=409, detail="run not finished")
        paths = write_all(job.result, job.out_dir)
        artifacts = {k: Path(v).name for k, v in paths.items()}
        writeback_info = {"written": None, "fallback": False}
        if job.questionnaire_path and Path(job.questionnaire_path).suffix.lower() in {
            ".xlsx", ".xlsm", ".docx"
        } and has_answer_anchors(job.result):
            wb = write_back(job.result, job.questionnaire_path, str(job.out_dir))
            writeback_info = {
                "written": Path(wb["written"]).name if wb.get("written") else None,
                "fallback": bool(wb.get("fallback")),
                "reason": wb.get("reason"),
            }
            if writeback_info["written"]:
                artifacts["writeback"] = writeback_info["written"]
        return {"artifacts": artifacts, "writeback": writeback_info}

    @app.get("/api/runs/{run_id}/download/{artifact}")
    def download(run_id: str, artifact: str):
        job = _get(run_id)
        # Prevent path traversal — only serve plain filenames from the run dir.
        safe = Path(artifact).name
        fp = job.out_dir / safe
        if not fp.exists():
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(str(fp), filename=safe)

    if _STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

    return app
