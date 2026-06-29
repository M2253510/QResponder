"""Web review UI backend tests (Phase 4, E2). Offline via TestClient + MockProvider.

The key one: every accept flows through the flywheel — using the UI trains the
Answer Library, edits train on the edited text, and re-accepting is idempotent.
"""

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from qresponder.config import Config  # noqa: E402
from qresponder.kb.library import AnswerLibrary  # noqa: E402
from qresponder.models import (  # noqa: E402
    AnswerResult,
    AnswerType,
    Citation,
    Confidence,
    InterpretationOption,
    QuestionnaireResult,
    ReviewReason,
    Status,
)
from qresponder.web.app import _Job, create_app  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _client(tmp_path):
    cfg = Config(llm_provider="mock", kb_mode="in_context")
    cfg.extra["web_runs_dir"] = str(tmp_path / "runs")
    return TestClient(create_app(cfg))


def _start_run(client, qa_path, tags="soc2"):
    with open(FIX / "sample.xlsx", "rb") as fh:
        resp = client.post(
            "/api/runs",
            files={"questionnaire": ("sample.xlsx", fh.read())},
            data={"kb": str(FIX / "kb"), "qa": str(qa_path), "tags": tags,
                  "evidence": str(FIX / "evidence")},
        )
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    for _ in range(50):
        r = client.get(f"/api/runs/{run_id}")
        if r.json()["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    data = client.get(f"/api/runs/{run_id}").json()
    assert data["status"] == "done", data
    return run_id, data


def test_status_never_leaks_key(tmp_path):
    client = _client(tmp_path)
    body = client.get("/api/status").json()
    assert body["provider"] == "mock"
    assert "model" in body
    assert "key" not in str(body).lower() or "api_key" not in body


def test_accept_trains_library_edit_and_idempotent(tmp_path):
    qa = tmp_path / "qa.yaml"
    client = _client(tmp_path)
    run_id, data = _start_run(client, qa)

    # Pick a generated answered item (not attachment) to accept with an edit.
    gen = next(r for r in data["results"]
               if r["answer_type"] != "attachment" and "incident" in r["question_text"].lower())
    qid = gen["question_id"]

    edited = "Yes — we maintain a documented, annually-tested incident response plan."
    r1 = client.post(f"/api/runs/{run_id}/items/{qid}/accept", json={"answer": edited})
    assert r1.status_code == 200
    body = r1.json()
    assert body["trained"] is True
    assert body["item"]["status"] == "answered"

    lib = AnswerLibrary.load(qa)
    entry = next(e for e in lib.entries if "incident" in e.question.lower())
    assert entry.answer == edited           # trained on the EDITED text, not the draft
    assert entry.version == 1

    # Re-accept the SAME item with the SAME text -> idempotent (no version bump).
    r2 = client.post(f"/api/runs/{run_id}/items/{qid}/accept", json={"answer": edited})
    assert r2.json()["trained"] is True
    lib2 = AnswerLibrary.load(qa)
    entry2 = next(e for e in lib2.entries if "incident" in e.question.lower())
    assert entry2.version == 1               # NOT bumped again

    # Editing again with new text DOES update + bump.
    r3 = client.post(f"/api/runs/{run_id}/items/{qid}/accept",
                     json={"answer": edited + " Reviewed quarterly."})
    assert r3.json()["library"]["action"] == "updated"
    lib3 = AnswerLibrary.load(qa)
    entry3 = next(e for e in lib3.entries if "incident" in e.question.lower())
    assert entry3.version == 2


def test_attachment_accept_not_trained(tmp_path):
    qa = tmp_path / "qa.yaml"
    client = _client(tmp_path)
    run_id, data = _start_run(client, qa)
    att = next((r for r in data["results"] if r["answer_type"] == "attachment"), None)
    assert att is not None
    qid = att["question_id"]
    resp = client.post(f"/api/runs/{run_id}/items/{qid}/accept",
                       json={"attachment": "SOC2_Type_II_Report_v2.md"})
    body = resp.json()
    assert body["item"]["status"] == "answered"
    assert body["item"]["attachment_path"] == "SOC2_Type_II_Report_v2.md"
    assert body["trained"] is False  # files aren't reusable Q&A — not sent to the flywheel


def test_export_writes_artifacts(tmp_path):
    qa = tmp_path / "qa.yaml"
    client = _client(tmp_path)
    run_id, _ = _start_run(client, qa)
    resp = client.post(f"/api/runs/{run_id}/export")
    assert resp.status_code == 200
    body = resp.json()
    assert "results.json" in body["artifacts"].values()
    assert "answered.xlsx" in body["artifacts"].values()
    assert "review.md" in body["artifacts"].values()
    assert "writeback" in body  # write-back attempted (sample has answer anchors)

    # Download one artifact.
    dl = client.get(f"/api/runs/{run_id}/download/results.json")
    assert dl.status_code == 200
    assert b"source_file" in dl.content


def test_static_page_loads_no_external_assets(tmp_path):
    client = _client(tmp_path)
    blob = ""
    for path in ("/", "/styles.css", "/app.js"):
        r = client.get(path)
        assert r.status_code == 200, path
        blob += r.text.lower()
    # No CDN / external fonts / remote scripts — the localhost promise holds.
    for needle in ("http://", "https://", "//cdn", "googleapis", "fonts.g", "unpkg", "jsdelivr"):
        assert needle not in blob, f"external asset reference found: {needle}"


def _inject_special_run(client, tmp_path):
    qa = tmp_path / "qa.yaml"
    job = _Job("special", tmp_path / "out", str(qa), tags=["soc2"])
    job.status = "done"
    job.result = QuestionnaireResult(
        source_file="x.xlsx",
        results=[
            AnswerResult(
                question_id="qa", question_text="Describe your encryption practices.",
                answer="", answer_type=AnswerType.TEXT, confidence=Confidence.LOW,
                status=Status.NEEDS_REVIEW, review_reason=ReviewReason.AMBIGUOUS,
                candidates=[
                    InterpretationOption(interpretation="at rest",
                                         answer="Encrypted at rest with AES-256.",
                                         citations=[Citation(source="enc.md", snippet="AES-256")],
                                         status=Status.ANSWERED),
                    InterpretationOption(interpretation="in transit",
                                         answer="TLS 1.2+ in transit.", status=Status.ANSWERED),
                ],
            ),
            AnswerResult(
                question_id="qc", question_text="What TLS version do you require?",
                answer="We require TLS 1.2.", answer_type=AnswerType.TEXT,
                confidence=Confidence.LOW, status=Status.NEEDS_REVIEW,
                review_reason=ReviewReason.CONFLICT, conflict_with="Answer Library: \"TLS 1.3 only\"",
            ),
        ],
    )
    client.app.state.jobs["special"] = job
    return qa


def test_accept_records_human_action_in_audit(tmp_path):
    """Part B: accepting (with an edit) stamps the audit trail's human_action."""
    qa = tmp_path / "qa.yaml"
    client = _client(tmp_path)
    run_id, data = _start_run(client, qa)
    gen = next(r for r in data["results"]
               if r["answer_type"] != "attachment" and "incident" in r["question_text"].lower())
    qid = gen["question_id"]
    res = client.post(f"/api/runs/{run_id}/items/{qid}/accept",
                      json={"answer": "Edited final answer.", "approved_by": "alice"}).json()
    ha = res["item"]["audit"]["human_action"]
    assert ha["type"] == "edited"
    assert ha["by"] == "alice"
    assert ha["at"]
    assert ha["original_answer"]  # the pre-edit draft preserved
    # Audit endpoint emits the pack.
    a = client.post(f"/api/runs/{run_id}/audit").json()
    assert "audit.json" in a["artifacts"].values()
    assert "audit.md" in a["artifacts"].values()


def test_special_cases_resolve_via_api(tmp_path):
    client = _client(tmp_path)
    qa = _inject_special_run(client, tmp_path)

    # Ambiguous -> pick an interpretation.
    r1 = client.post("/api/runs/special/items/qa/accept",
                     json={"interpretation": "at rest"})
    item = r1.json()["item"]
    assert item["status"] == "answered"
    assert "AES-256" in item["answer"]
    assert r1.json()["trained"] is True

    # Conflict -> reconcile with an edit.
    r2 = client.post("/api/runs/special/items/qc/accept",
                     json={"answer": "We require TLS 1.2 or higher (1.3 preferred)."})
    item2 = r2.json()["item"]
    assert item2["status"] == "answered"
    assert item2["review_reason"] == "none"
    assert item2["conflict_with"] is None

    # Both resolved -> export succeeds.
    exp = client.post("/api/runs/special/export").json()
    assert "results.json" in exp["artifacts"].values()

    # Library trained from both text answers.
    lib = AnswerLibrary.load(qa)
    assert len(lib.entries) == 2
