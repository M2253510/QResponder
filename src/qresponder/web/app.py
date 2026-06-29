"""FastAPI backend for the local web review UI (Phases 4-5).

Thin orchestration over the engine. Phase 5 adds named workspaces (isolated
asset bundles) and asset-management endpoints so a stranger can configure
everything — model check, KB, evidence, approved answers, settings — from the
browser, without editing a file. The provider API key is the ONE exception: it
stays in .env/global config and is never accepted, stored, or returned here.

The web layer reimplements no engine logic — it writes workspace files and calls
run_pipeline / approve_one / writer / writeback / doctor. Binds 127.0.0.1.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import Counter
from pathlib import Path

from fastapi import Body, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import Config, load_config
from ..core.flywheel import approve_one, write_library
from ..core.pipeline import run_pipeline
from ..core.workspace import WorkspaceError, WorkspaceStore
from ..kb.evidence import EvidenceIndex
from ..kb.library import AnswerLibrary, LibraryEntry
from ..kb.tags import load_tag_sidecar, normalize_tags, parse_tags, write_tag_sidecar
from ..models import AnswerType, QuestionnaireResult, ReviewReason, Status
from ..output.writer import write_all
from ..output.writeback import has_answer_anchors, write_back

log = logging.getLogger("qresponder.web")

_STATIC_DIR = Path(__file__).parent / "static"

# Upload allow-lists (extension sandbox). KB is cited as answer text; evidence is
# attached to "please attach…" fields, so it allows a few more document types.
_KB_EXTS = {".txt", ".md", ".markdown", ".rst", ".pdf", ".docx"}
_EVIDENCE_EXTS = _KB_EXTS | {".xlsx", ".xlsm", ".csv", ".png", ".jpg", ".jpeg", ".pptx"}


def _safe_filename(name: str) -> str:
    """Strip any path components — uploads never escape their workspace dir."""
    base = Path(name or "").name.strip()
    if not base or base.startswith(".") or base in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    return base


# --- in-memory run registry --------------------------------------------------

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
        self.approved: dict[str, str] = {}  # qid -> approved text (idempotent re-accept)


class AcceptBody(BaseModel):
    answer: str | None = None
    interpretation: str | None = None
    attachment: str | None = None
    approved_by: str | None = "web"


def _summary(result: QuestionnaireResult) -> dict:
    answered = [r for r in result.results if r.status == Status.ANSWERED]
    high = sum(1 for r in answered if r.confidence.value == "high")
    flagged = [r for r in result.results if r.status == Status.NEEDS_REVIEW]
    return {
        "total": len(result.results),
        "answered": len(answered),
        "auto_answered_high": high,
        "flagged": len(flagged),
        "flagged_by_reason": dict(Counter(r.review_reason.value for r in flagged)),
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
    app.state.jobs = jobs  # test seam
    store = WorkspaceStore(config.extra.get("workspaces_dir") or config.workspaces_dir)
    app.state.store = store

    # ---- run machinery (shared by legacy + workspace runs) -----------------
    def _run(job: _Job, kb, evidence, qa, cfg: Config):
        job.status = "running"
        try:
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

    def _start_job(out_dir: Path, qa_path: str, tags, questionnaire: UploadFile,
                   data: bytes, kb, evidence, cfg: Config) -> str:
        run_id = uuid.uuid4().hex[:12]
        out_dir.mkdir(parents=True, exist_ok=True)
        job = _Job(run_id, out_dir, qa_path, normalize_tags(tags))
        dest = out_dir / _safe_filename(questionnaire.filename or "questionnaire")
        dest.write_bytes(data)
        job.questionnaire_path = str(dest)
        jobs[run_id] = job
        threading.Thread(target=_run, args=(job, kb, evidence, qa_path, cfg), daemon=True).start()
        return run_id

    def _get_job(run_id: str) -> _Job:
        job = jobs.get(run_id)
        if job is None:
            raise HTTPException(status_code=404, detail="run not found")
        return job

    def _ws(workspace_id: str):
        try:
            return store.get(workspace_id)
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    # ---- status / doctor ---------------------------------------------------
    @app.get("/api/status")
    def status():
        provider = config.llm_provider
        model = config.anthropic_model if provider == "anthropic" else config.llm_model
        return {"provider": provider, "model": model, "kb_mode": config.kb_mode}

    @app.get("/api/doctor")
    def doctor():
        """Live connection check (the wizard's Test connection). Never the key."""
        from ..llm.doctor import run_doctor

        checks = run_doctor(config)
        return {
            "ok": all(c.ok for c in checks),
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
        }

    # ---- workspaces CRUD ---------------------------------------------------
    def _ws_view(ws) -> dict:
        return {
            "id": ws.id, "name": ws.name, "created": ws.created,
            "settings": ws.load_settings(),
            "kb": _list_dir(ws.kb_dir), "evidence": _list_dir(ws.evidence_dir),
            "qa_count": len(AnswerLibrary.load(ws.qa_path).entries),
        }

    def _list_dir(d: Path) -> list[dict]:
        sidecar = load_tag_sidecar(d)
        out = []
        if d.exists():
            for fp in sorted(d.iterdir()):
                if fp.is_file() and fp.name != ".tags.yaml":
                    out.append({"name": fp.name, "tags": sidecar.get(fp.name, [])})
        return out

    @app.post("/api/workspaces")
    def create_ws(body: dict = Body(...)):
        try:
            ws = store.create(str(body.get("name", "")).strip())
        except WorkspaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return _ws_view(ws)

    @app.get("/api/workspaces")
    def list_ws():
        return [{"id": w.id, "name": w.name, "created": w.created} for w in store.list()]

    @app.get("/api/workspaces/{wid}")
    def get_ws(wid: str):
        return _ws_view(_ws(wid))

    @app.patch("/api/workspaces/{wid}")
    def rename_ws(wid: str, body: dict = Body(...)):
        _ws(wid)
        name = str(body.get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name required")
        return _ws_view(store.rename(wid, name))

    @app.delete("/api/workspaces/{wid}")
    def delete_ws(wid: str):
        _ws(wid)
        store.delete(wid)
        return {"deleted": wid}

    # ---- KB / evidence assets ---------------------------------------------
    def _upload(dest_dir: Path, files: list[UploadFile], allowed: set[str]) -> list[dict]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            name = _safe_filename(f.filename or "")
            ext = Path(name).suffix.lower()
            if ext not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{name}': unsupported type '{ext or '(none)'}'. "
                           f"Allowed: {', '.join(sorted(allowed))}.",
                )
            (dest_dir / name).write_bytes(f.file.read())
        return _list_dir(dest_dir)

    @app.post("/api/workspaces/{wid}/kb")
    def upload_kb(wid: str, files: list[UploadFile]):
        return {"files": _upload(_ws(wid).kb_dir, files, _KB_EXTS)}

    @app.get("/api/workspaces/{wid}/kb")
    def list_kb(wid: str):
        return {"files": _list_dir(_ws(wid).kb_dir)}

    @app.delete("/api/workspaces/{wid}/kb/{filename}")
    def delete_kb(wid: str, filename: str):
        return {"files": _delete_asset(_ws(wid).kb_dir, filename)}

    @app.patch("/api/workspaces/{wid}/kb/{filename}")
    def tag_kb(wid: str, filename: str, body: dict = Body(...)):
        return {"files": _set_tags(_ws(wid).kb_dir, filename, body.get("tags"))}

    @app.post("/api/workspaces/{wid}/evidence")
    def upload_evidence(wid: str, files: list[UploadFile]):
        return {"files": _upload(_ws(wid).evidence_dir, files, _EVIDENCE_EXTS)}

    @app.get("/api/workspaces/{wid}/evidence")
    def list_evidence(wid: str):
        return {"files": _list_dir(_ws(wid).evidence_dir)}

    @app.delete("/api/workspaces/{wid}/evidence/{filename}")
    def delete_evidence(wid: str, filename: str):
        return {"files": _delete_asset(_ws(wid).evidence_dir, filename)}

    @app.patch("/api/workspaces/{wid}/evidence/{filename}")
    def tag_evidence(wid: str, filename: str, body: dict = Body(...)):
        return {"files": _set_tags(_ws(wid).evidence_dir, filename, body.get("tags"))}

    def _delete_asset(d: Path, filename: str) -> list[dict]:
        fp = d / _safe_filename(filename)
        if not fp.exists():
            raise HTTPException(status_code=404, detail="file not found")
        fp.unlink()
        sidecar = load_tag_sidecar(d)
        if fp.name in sidecar:
            del sidecar[fp.name]
            write_tag_sidecar(d, sidecar)
        return _list_dir(d)

    def _set_tags(d: Path, filename: str, tags) -> list[dict]:
        safe = _safe_filename(filename)
        if not (d / safe).exists():
            raise HTTPException(status_code=404, detail="file not found")
        sidecar = load_tag_sidecar(d)
        sidecar[safe] = normalize_tags(tags)
        write_tag_sidecar(d, sidecar)
        return _list_dir(d)

    # ---- approved answers (qa) CRUD ---------------------------------------
    @app.get("/api/workspaces/{wid}/qa")
    def list_qa(wid: str):
        lib = AnswerLibrary.load(_ws(wid).qa_path)
        return {"entries": [
            {"index": i, "question": e.question, "answer": e.answer, "tags": e.tags,
             "approved_by": e.approved_by, "version": e.version}
            for i, e in enumerate(lib.entries)
        ]}

    @app.post("/api/workspaces/{wid}/qa")
    def add_qa(wid: str, body: dict = Body(...)):
        ws = _ws(wid)
        q = str(body.get("question", "")).strip()
        a = str(body.get("answer", "")).strip()
        if not q or not a:
            raise HTTPException(status_code=400, detail="question and answer are required")
        approve_one(q, a, ws.qa_path, approved_by=body.get("approved_by") or "web",
                    tags=body.get("tags"))
        return list_qa(wid)

    @app.put("/api/workspaces/{wid}/qa/{index}")
    def edit_qa(wid: str, index: int, body: dict = Body(...)):
        ws = _ws(wid)
        lib = AnswerLibrary.load(ws.qa_path)
        if not (0 <= index < len(lib.entries)):
            raise HTTPException(status_code=404, detail="entry not found")
        e = lib.entries[index]
        if "question" in body: e.question = str(body["question"]).strip()
        if "answer" in body: e.answer = str(body["answer"]).strip()
        if "tags" in body: e.tags = normalize_tags(body["tags"])
        e.version += 1
        write_library(ws.qa_path, lib.entries)
        return list_qa(wid)

    @app.delete("/api/workspaces/{wid}/qa/{index}")
    def delete_qa(wid: str, index: int):
        ws = _ws(wid)
        lib = AnswerLibrary.load(ws.qa_path)
        if not (0 <= index < len(lib.entries)):
            raise HTTPException(status_code=404, detail="entry not found")
        del lib.entries[index]
        write_library(ws.qa_path, lib.entries)
        return list_qa(wid)

    # ---- per-workspace settings -------------------------------------------
    @app.get("/api/workspaces/{wid}/settings")
    def get_settings(wid: str):
        return {"settings": _ws(wid).load_settings()}

    @app.patch("/api/workspaces/{wid}/settings")
    def update_settings(wid: str, body: dict = Body(...)):
        _ws(wid)
        try:
            settings = store.update_settings(wid, body or {})
        except WorkspaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"settings": settings}

    # ---- workspace runs ----------------------------------------------------
    @app.post("/api/workspaces/{wid}/runs")
    async def create_ws_run(wid: str, questionnaire: UploadFile, mode: str = Form(None),
                            tags: str = Form(None)):
        ws = _ws(wid)
        cfg = ws.effective_config(config)
        if mode:
            cfg.kb_mode = mode
        scope = parse_tags(tags) if tags else ws.default_tags()
        out_dir = ws.runs_dir / uuid.uuid4().hex[:12]
        data = await questionnaire.read()
        run_id = _start_job(
            out_dir, str(ws.qa_path), scope, questionnaire, data,
            str(ws.kb_dir), str(ws.evidence_dir), cfg,
        )
        return {"run_id": run_id, "workspace": wid}

    # ---- legacy (non-workspace) run: explicit paths ------------------------
    @app.post("/api/runs")
    async def create_run(questionnaire: UploadFile, kb: str = Form(None),
                         evidence: str = Form(None), qa: str = Form(None),
                         tags: str = Form(None), mode: str = Form(None)):
        out_dir = Path(config.extra.get("web_runs_dir", "web_runs")) / uuid.uuid4().hex[:12]
        qa_path = qa or str(out_dir / "qa.yaml")
        cfg = config.model_copy()
        if mode:
            cfg.kb_mode = mode
        data = await questionnaire.read()
        run_id = _start_job(out_dir, qa_path, parse_tags(tags), questionnaire, data, kb, evidence, cfg)
        return {"run_id": run_id}

    # ---- run status / accept / export / download (shared) ------------------
    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        job = _get_job(run_id)
        payload = {"status": job.status, "error": job.error}
        if job.result is not None:
            payload["summary"] = _summary(job.result)
            payload["results"] = [r.model_dump() for r in job.result.results]
            payload["approved"] = list(job.approved.keys())
        return payload

    @app.post("/api/runs/{run_id}/items/{qid}/accept")
    def accept(run_id: str, qid: str, body: AcceptBody):
        job = _get_job(run_id)
        if job.result is None:
            raise HTTPException(status_code=409, detail="run not finished")
        item = next((r for r in job.result.results if r.question_id == qid), None)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")

        original = item.answer  # draft, before any edit
        is_attachment = item.answer_type == AnswerType.ATTACHMENT or bool(body.attachment)
        if body.attachment:
            item.attachment_path = body.attachment
            item.answer = body.attachment
            item.answer_type = AnswerType.ATTACHMENT
            final_answer = body.attachment
            action_type = "attached"
        elif body.interpretation:
            chosen = next((c for c in item.candidates if c.interpretation == body.interpretation), None)
            final_answer = (body.answer or (chosen.answer if chosen else "")).strip()
            if chosen is not None:
                item.citations = chosen.citations
            item.answer = final_answer
            action_type = "picked"
        else:
            final_answer = (body.answer if body.answer is not None else item.answer).strip()
            action_type = "edited" if final_answer != (original or "").strip() else "accepted"
            item.answer = final_answer

        item.status = Status.ANSWERED
        item.review_reason = ReviewReason.NONE
        item.conflict_with = None
        if not is_attachment:
            from ..models import Confidence

            item.confidence = Confidence.HIGH  # human-approved is the highest authority
        # Capture the human action in the audit trail (Part B).
        from datetime import datetime, timezone

        from ..models import AuditTrail, HumanAction

        if item.audit is None:
            item.audit = AuditTrail(cited=list(item.citations))
        item.audit.human_action = HumanAction(
            type=action_type, by=body.approved_by,
            at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            original_answer=original if action_type == "edited" else None,
        )
        _persist(job)

        trained = False
        library = None
        if not is_attachment and final_answer:
            if job.approved.get(qid) != final_answer:
                library = approve_one(item.question_text, final_answer, job.qa_path,
                                      approved_by=body.approved_by, tags=job.tags)
                job.approved[qid] = final_answer
            trained = True

        return {"item": item.model_dump(), "trained": trained, "library": library}

    @app.post("/api/runs/{run_id}/export")
    def export(run_id: str):
        job = _get_job(run_id)
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
                "fallback": bool(wb.get("fallback")), "reason": wb.get("reason"),
            }
            if writeback_info["written"]:
                artifacts["writeback"] = writeback_info["written"]
        return {"artifacts": artifacts, "writeback": writeback_info}

    @app.post("/api/runs/{run_id}/audit")
    def audit(run_id: str):
        from ..output.audit import write_audit

        job = _get_job(run_id)
        if job.result is None:
            raise HTTPException(status_code=409, detail="run not finished")
        paths = write_audit(job.result, job.out_dir)
        return {"artifacts": {k: Path(v).name for k, v in paths.items()}}

    @app.get("/api/runs/{run_id}/download/{artifact}")
    def download(run_id: str, artifact: str):
        job = _get_job(run_id)
        fp = job.out_dir / Path(artifact).name  # sanitize: filename only
        if not fp.exists():
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(str(fp), filename=fp.name)

    if _STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

    return app
