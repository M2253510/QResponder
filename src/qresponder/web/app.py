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
# Bulk-ingest allow-lists (Phase 8 C) — "any format" = this set (+ .zip expands).
_KB_INGEST_EXTS = {".txt", ".md", ".markdown", ".rst", ".csv", ".pdf", ".docx",
                   ".xlsx", ".xlsm", ".html", ".htm"}
_EVIDENCE_INGEST_EXTS = _KB_INGEST_EXTS | {".png", ".jpg", ".jpeg", ".pptx"}
_QA_INGEST_EXTS = {".csv", ".json", ".xlsx", ".xlsm", ".md", ".markdown", ".txt", ".docx"}


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
        self.history: list = []           # prior submissions (G1)
        self.history_path: str | None = None  # where to append on export
        self.preset: str | None = None    # answer-style preset name (Phase 7 A)
        self.style: str | None = None     # resolved preset instructions
        self.review_markers: bool = True  # mark NEEDS_REVIEW cells on export (Phase 7 C)
        self.provider_obj = None          # explicit LLM provider (Phase 8) — no mock fallback
        self.events: list = []            # live progress events (Phase 8 D dashboard)
        self.n_files = 1                  # for batch dashboards
        self.zip_name: str | None = None  # batch zip artifact
        self.workspace_id: str | None = None  # owning workspace (Phase 8 E)


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


def create_app(config: Config | None = None, model_fetch=None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(title="QRESPONDER review UI")
    jobs: dict[str, _Job] = {}
    app.state.jobs = jobs  # test seam
    resolved: dict[tuple, str] = {}  # (wid, question) -> answer, for idempotent resolve
    app.state.model_fetch = model_fetch  # injectable HTTP fetcher for model lists (tests)
    store = WorkspaceStore(config.extra.get("workspaces_dir") or config.workspaces_dir)
    app.state.store = store

    # ---- run machinery (shared by legacy + workspace runs) -----------------
    def _emit(job: _Job, event: dict):
        import time

        job.events.append({"t": round(time.time(), 3), **event})

    def _run(job: _Job, kb, evidence, qa, cfg: Config):
        job.status = "running"
        try:
            result = run_pipeline(
                job.questionnaire_path, kb, qa, cfg,
                scope_tags=job.tags, evidence_dir=evidence, history=job.history,
                preset=job.preset, style=job.style, provider=job.provider_obj,
                on_event=lambda e: _emit(job, e),
            )
            job.result = result
            _persist(job)
            job.status = "done"
        except Exception as exc:  # noqa: BLE001
            log.exception("Run %s failed", job.run_id)
            job.error = str(exc)
            job.status = "error"

    def _build_provider(provider_name: str | None, model: str | None):
        """Build the selected provider — NO silent mock fallback. Raises a clear
        error if unconfigured (the run is blocked, never auto-mocked)."""
        from ..llm.providers import canonical, is_configured, make_provider_for

        p = canonical(provider_name or config.llm_provider)
        if not is_configured(config, p):
            raise HTTPException(
                status_code=400,
                detail=f"Provider '{p}' is not configured — set its key in .env or pick another.")
        try:
            return make_provider_for(config, p, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"{p} unavailable: {exc}")

    def _start_job(out_dir: Path, qa_path: str, tags, questionnaire: UploadFile,
                   data: bytes, kb, evidence, cfg: Config, history=None, history_path=None,
                   preset=None, style=None, provider_obj=None) -> str:
        run_id = uuid.uuid4().hex[:12]
        out_dir.mkdir(parents=True, exist_ok=True)
        job = _Job(run_id, out_dir, qa_path, normalize_tags(tags))
        job.history = history or []
        job.history_path = history_path
        job.preset = preset
        job.style = style
        job.provider_obj = provider_obj
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

    # ---- status / providers / doctor --------------------------------------
    @app.get("/api/status")
    def status():
        from ..llm.models import reachable
        from ..llm.providers import canonical, model_for

        provider = canonical(config.llm_provider)
        model = model_for(config, provider, None)
        if provider == "mock":
            active, reason = True, "mock provider (dev/test)"
        else:
            active, reason = reachable(provider, config, fetch=app.state.model_fetch)
        # No key, ever — only provider/model names + a liveness flag.
        return {"provider": provider, "model": model, "kb_mode": config.kb_mode,
                "active": active, "reason": reason}

    @app.get("/api/providers")
    def providers():
        from ..llm.models import list_models
        from ..llm.providers import PROVIDER_SPECS, is_configured

        out = []
        for name, spec in PROVIDER_SPECS.items():
            configured = is_configured(config, name)
            entry = {"name": name, "label": spec["label"], "configured": configured,
                     "reachable": False, "models": [], "reason": None}
            if configured:
                ml = list_models(name, config, fetch=app.state.model_fetch)
                entry["models"] = [m.to_dict() for m in ml.models]
                entry["reachable"] = ml.reason is None
                entry["reason"] = ml.reason
            else:
                entry["reason"] = f"set the {name} key in .env"
            out.append(entry)  # never includes a key
        return out

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
                if fp.is_file() and not fp.name.startswith("."):  # skip sidecars
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

    # ---- KB / evidence assets (Phase 8 C: bulk, per-file feedback, zip) -----
    def _bulk_upload(dest_dir: Path, files: list[UploadFile], allowed: set[str], tags=None) -> dict:
        from ..core.bulk_ingest import ingest_files

        items = [(f.filename or "", f.file.read()) for f in files]
        return ingest_files(items, dest_dir, allowed, tags=tags)

    @app.post("/api/workspaces/{wid}/kb")
    def upload_kb(wid: str, files: list[UploadFile], tags: str = Form(None)):
        res = _bulk_upload(_ws(wid).kb_dir, files, _KB_INGEST_EXTS, tags=parse_tags(tags))
        return res  # {accepted, rejected, files}

    @app.get("/api/workspaces/{wid}/kb")
    def list_kb(wid: str):
        return {"files": _list_dir(_ws(wid).kb_dir)}

    @app.delete("/api/workspaces/{wid}/kb/{filename}")
    def delete_kb(wid: str, filename: str):
        return {"files": _delete_asset(_ws(wid).kb_dir, filename)}

    @app.patch("/api/workspaces/{wid}/kb/{filename}")
    def tag_kb(wid: str, filename: str, body: dict = Body(...)):
        return {"files": _set_tags(_ws(wid).kb_dir, filename, body.get("tags"))}

    @app.post("/api/workspaces/{wid}/connect")
    def connect_source(wid: str, body: dict = Body(...)):
        """Run a source connector (folder/website) into the workspace KB. Explicit
        only — connectors never fetch during answering."""
        from ..connectors.base import ConnectorError, ingest_connector

        ws = _ws(wid)
        kind = str(body.get("type", "")).lower()
        tags = parse_tags(body.get("tags"))
        try:
            if kind == "folder":
                from ..connectors.folder import FolderConnector

                conn = FolderConnector(str(body.get("path", "")), tags=tags)
            elif kind == "website":
                from ..connectors.website import WebsiteConnector

                conn = WebsiteConnector(str(body.get("url", "")), depth=int(body.get("depth", 1)),
                                        max_pages=int(body.get("max_pages", 20)),
                                        allow_private=bool(body.get("allow_private", False)), tags=tags)
            elif kind == "gdrive":
                from ..connectors.gdrive import GoogleDriveConnector

                conn = GoogleDriveConnector(str(body.get("folder_id", "")), tags=tags)
            else:
                raise HTTPException(status_code=400, detail="type must be folder|website|gdrive")
            res = ingest_connector(conn, ws.kb_dir, tags=tags)
        except ConnectorError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return res

    @app.post("/api/workspaces/{wid}/evidence")
    def upload_evidence(wid: str, files: list[UploadFile], tags: str = Form(None)):
        return _bulk_upload(_ws(wid).evidence_dir, files, _EVIDENCE_INGEST_EXTS, tags=parse_tags(tags))

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

    @app.post("/api/workspaces/{wid}/qa/import")
    def import_qa_files(wid: str, files: list[UploadFile], tags: str = Form(None)):
        """Bulk-import approved answers from CSV/JSON/XLSX/MD/DOCX → approve_one."""
        from ..core.qa_import import import_qa

        ws = _ws(wid)
        accepted, rejected = [], []
        for f in files:
            ext = Path(f.filename or "").suffix.lower()
            if ext not in _QA_INGEST_EXTS:
                rejected.append({"name": f.filename, "reason": f"unsupported Q&A format '{ext}'"})
            else:
                accepted.append((f.filename or "", f.file.read()))
        res = import_qa(accepted, ws.qa_path, approved_by="import", tags=parse_tags(tags))
        res["rejected"] = rejected
        res["total"] = len(AnswerLibrary.load(ws.qa_path).entries)
        return res

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

    @app.get("/api/workspaces/{wid}/kb-check")
    def kb_check(wid: str):
        from ..core.kb_health import check_library

        ws = _ws(wid)
        return check_library(ws.qa_path, config=config)

    # ---- cross-file flagged aggregation + one-click resolve (Phase 8 E) ----
    def _ws_flagged(wid: str):
        """All NEEDS_REVIEW items across this workspace's finished runs."""
        occ = []
        for rid, job in jobs.items():
            if job.workspace_id != wid or job.result is None:
                continue
            fname = Path(job.questionnaire_path).name if job.questionnaire_path else rid
            for r in job.result.results:
                if r.status == Status.NEEDS_REVIEW:
                    occ.append((rid, job, r, fname))
        return occ

    @app.get("/api/workspaces/{wid}/flagged")
    def flagged(wid: str):
        from ..kb.base import lexical_similarity

        _ws(wid)
        floor = getattr(config, "dedup_threshold", 0.9)
        groups: list[dict] = []
        for rid, job, r, fname in _ws_flagged(wid):
            o = {"run_id": rid, "qid": r.question_id, "file": fname}
            placed = False
            for g in groups:
                if lexical_similarity(r.question_text, g["question"]) >= floor:
                    g["occurrences"].append(o)
                    if not g["draft"] and r.answer:
                        g["draft"] = r.answer
                    placed = True
                    break
            if not placed:
                groups.append({"question": r.question_text, "reason": r.review_reason.value,
                               "draft": r.answer or "", "occurrences": [o]})
        for g in groups:
            g["count"] = len(g["occurrences"])
            g["files"] = sorted({o["file"] for o in g["occurrences"]})
        return {"groups": groups}

    @app.post("/api/workspaces/{wid}/flagged/resolve")
    def resolve_flagged(wid: str, body: dict = Body(...)):
        from ..kb.base import lexical_similarity
        from ..models import Citation, Confidence

        ws = _ws(wid)
        floor = getattr(config, "dedup_threshold", 0.9)
        q = str(body.get("question", "")).strip()
        a = str(body.get("answer", "")).strip()
        if not q or not a:
            raise HTTPException(status_code=400, detail="question and answer are required")

        updated, files, touched = 0, set(), set()
        for rid, job, r, fname in _ws_flagged(wid):
            if lexical_similarity(r.question_text, q) >= floor:
                r.status = Status.ANSWERED
                r.answer = a
                r.review_reason = ReviewReason.NONE
                r.conflict_with = None
                r.confidence = Confidence.HIGH
                r.citations = [Citation(source="cross-file resolve (human)", snippet=a, faithful=True)]
                updated += 1
                files.add(fname)
                touched.add(rid)
        for rid in touched:
            _persist(jobs[rid])

        # Train the library ONCE (idempotent per workspace+question — no spurious
        # version bumps on re-resolve with the same text).
        key = (wid, q.lower())
        trained, library = False, None
        if resolved.get(key) != a:
            library = approve_one(q, a, ws.qa_path,
                                  approved_by=body.get("approved_by") or "cross-file", tags=body.get("tags"))
            resolved[key] = a
            trained = True
        return {"updated": updated, "files": sorted(files), "trained": trained, "library": library}

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

    # ---- answer-style presets (Phase 7 A) ---------------------------------
    @app.get("/api/workspaces/{wid}/presets")
    def list_presets(wid: str):
        from ..core.presets import BUILTIN_PRESETS, load_workspace_presets

        ws = _ws(wid)
        return {"builtin": BUILTIN_PRESETS, "custom": load_workspace_presets(ws.path)}

    @app.post("/api/workspaces/{wid}/presets")
    def add_preset(wid: str, body: dict = Body(...)):
        from ..core.presets import load_workspace_presets, save_workspace_preset

        ws = _ws(wid)
        name = str(body.get("name", "")).strip()
        instructions = str(body.get("instructions", "")).strip()
        if not name or not instructions:
            raise HTTPException(status_code=400, detail="name and instructions are required")
        save_workspace_preset(ws.path, name, instructions)
        return {"custom": load_workspace_presets(ws.path)}

    # ---- workspace runs ----------------------------------------------------
    @app.post("/api/workspaces/{wid}/runs")
    async def create_ws_run(wid: str, questionnaire: UploadFile, mode: str = Form(None),
                            tags: str = Form(None), preset: str = Form(None),
                            provider: str = Form(None), model: str = Form(None)):
        ws = _ws(wid)
        cfg = ws.effective_config(config)
        if mode:
            cfg.kb_mode = mode
        scope = parse_tags(tags) if tags else ws.default_tags()
        out_dir = ws.runs_dir / uuid.uuid4().hex[:12]
        data = await questionnaire.read()
        from ..core.history import HistoryStore
        from ..core.presets import resolve as resolve_preset

        settings = ws.load_settings()
        preset_name = preset or settings.get("preset")
        style = resolve_preset(preset_name, ws.path)
        # Build the selected provider up front — blocks (400) on misconfig, never mocks.
        provider_obj = _build_provider(provider, model or settings.get("model"))
        hist_path = ws.path / "history.yaml"
        run_id = _start_job(
            out_dir, str(ws.qa_path), scope, questionnaire, data,
            str(ws.kb_dir), str(ws.evidence_dir), cfg,
            history=HistoryStore(hist_path).load(), history_path=str(hist_path),
            preset=preset_name if style else None, style=style, provider_obj=provider_obj,
        )
        jobs[run_id].review_markers = bool(settings.get("review_markers", True))
        jobs[run_id].workspace_id = wid
        return {"run_id": run_id, "workspace": wid}

    # ---- Ask mode (Phase 10 A): one question, the same grounded path -------
    @app.post("/api/workspaces/{wid}/ask")
    def ask(wid: str, body: dict = Body(...)):
        from ..core.pipeline import run_ask
        from ..core.presets import resolve as resolve_preset

        ws = _ws(wid)
        cfg = ws.effective_config(config)
        if body.get("mode"):
            cfg.kb_mode = body["mode"]
        question = str(body.get("question", "")).strip()
        if not question:
            raise HTTPException(status_code=400, detail="question is required")
        provider_obj = _build_provider(body.get("provider"), body.get("model") or ws.load_settings().get("model"))
        settings = ws.load_settings()
        preset_name = body.get("preset") or settings.get("preset")
        style = resolve_preset(preset_name, ws.path)
        scope = parse_tags(body.get("tags")) if body.get("tags") else ws.default_tags()
        r = run_ask(question, str(ws.kb_dir), str(ws.qa_path), cfg, scope_tags=scope,
                    provider=provider_obj, evidence_dir=str(ws.evidence_dir),
                    preset=preset_name if style else None, style=style)
        return r.model_dump()

    # ---- workspace batch (Part D) -----------------------------------------
    @app.post("/api/workspaces/{wid}/batch")
    async def ws_batch(wid: str, files: list[UploadFile], provider: str = Form(None),
                       model: str = Form(None)):
        from ..core.batch import run_batch, zip_batch

        ws = _ws(wid)
        cfg = ws.effective_config(config)
        provider_obj = _build_provider(provider, model or ws.load_settings().get("model"))
        batch_id = "batch_" + uuid.uuid4().hex[:10]
        out_dir = ws.runs_dir / batch_id
        in_dir = out_dir / "_in"
        in_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in files:
            dest = in_dir / _safe_filename(f.filename or "questionnaire")
            dest.write_bytes(await f.read())
            saved.append(dest)
        summary = run_batch(saved, str(ws.kb_dir), str(ws.qa_path), cfg, out_dir,
                            scope_tags=ws.default_tags(), evidence_dir=str(ws.evidence_dir),
                            provider=provider_obj)
        zname = Path(zip_batch(out_dir)).name
        # Register a pseudo-job so the existing download route serves the zip.
        jobs[batch_id] = _Job(batch_id, out_dir, str(ws.qa_path), ws.default_tags())
        return {"batch_id": batch_id, "summary": summary, "zip": zname,
                "download": f"/api/runs/{batch_id}/download/{zname}"}

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

    # ---- live processing dashboard (Phase 8 D) ----------------------------
    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str):
        """Snapshot of progress events (the dashboard can poll this or use /stream)."""
        job = _get_job(run_id)
        return {"status": job.status, "error": job.error, "n_files": job.n_files,
                "zip": job.zip_name, "events": job.events,
                "summary": _summary(job.result) if job.result is not None else None}

    @app.get("/api/runs/{run_id}/stream")
    def run_stream(run_id: str):
        import json
        import time

        from fastapi.responses import StreamingResponse

        job = _get_job(run_id)

        def gen():
            i = 0
            while True:
                while i < len(job.events):
                    yield f"data: {json.dumps(job.events[i])}\n\n"
                    i += 1
                if job.status in ("done", "error"):
                    yield f"data: {json.dumps({'type': '_end', 'status': job.status})}\n\n"
                    return
                time.sleep(0.05)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/workspaces/{wid}/batch-stream")
    async def ws_batch_stream(wid: str, files: list[UploadFile], provider: str = Form(None),
                              model: str = Form(None)):
        """Background batch with a live event stream for the dashboard."""
        from ..core.batch import run_batch, zip_batch

        ws = _ws(wid)
        cfg = ws.effective_config(config)
        provider_obj = _build_provider(provider, model or ws.load_settings().get("model"))
        batch_id = "batch_" + uuid.uuid4().hex[:10]
        out_dir = ws.runs_dir / batch_id
        in_dir = out_dir / "_in"
        in_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in files:
            dest = in_dir / _safe_filename(f.filename or "questionnaire")
            dest.write_bytes(await f.read())
            saved.append(dest)
        job = _Job(batch_id, out_dir, str(ws.qa_path), ws.default_tags())
        job.n_files = len(saved)
        jobs[batch_id] = job

        def _go():
            job.status = "running"
            try:
                run_batch(saved, str(ws.kb_dir), str(ws.qa_path), cfg, out_dir,
                          scope_tags=ws.default_tags(), evidence_dir=str(ws.evidence_dir),
                          provider=provider_obj, on_event=lambda e: _emit(job, e))
                job.zip_name = Path(zip_batch(out_dir)).name
                job.status = "done"
            except Exception as exc:  # noqa: BLE001
                job.error = str(exc)
                job.status = "error"

        threading.Thread(target=_go, daemon=True).start()
        return {"batch_id": batch_id, "n_files": len(saved),
                "stream": f"/api/runs/{batch_id}/stream", "events": f"/api/runs/{batch_id}/events"}

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
        paths = write_all(job.result, job.out_dir, review_markers=job.review_markers)
        artifacts = {k: Path(v).name for k, v in paths.items()}
        writeback_info = {"written": None, "fallback": False}
        if job.questionnaire_path and Path(job.questionnaire_path).suffix.lower() in {
            ".xlsx", ".xlsm", ".docx"
        } and has_answer_anchors(job.result):
            wb = write_back(job.result, job.questionnaire_path, str(job.out_dir),
                            review_markers=job.review_markers)
            writeback_info = {
                "written": Path(wb["written"]).name if wb.get("written") else None,
                "fallback": bool(wb.get("fallback")), "reason": wb.get("reason"),
            }
            if writeback_info["written"]:
                artifacts["writeback"] = writeback_info["written"]
        # Record this submission in the workspace history (G1).
        if job.history_path:
            from datetime import datetime, timezone

            from ..core.history import HistoryStore

            HistoryStore(job.history_path).append(
                job.result, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
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
