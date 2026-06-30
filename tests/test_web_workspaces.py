"""Phase 5 workspace + asset-management web tests (offline, TestClient + mock).

Includes the headline acceptance: a fresh-clone, browser-only path that creates a
workspace, uploads assets, runs, accepts (training that workspace's qa.yaml), and
exports — without editing any file on disk by hand.
"""

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from qresponder.config import Config  # noqa: E402
from qresponder.kb.library import AnswerLibrary  # noqa: E402
from qresponder.web.app import create_app  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
INCIDENT_MD = "Tags: soc2\n\nWe maintain a documented incident response plan with severity levels, on-call roles, and a post-incident review."


def _client(tmp_path):
    cfg = Config(llm_provider="mock", kb_mode="in_context")
    cfg.extra["workspaces_dir"] = str(tmp_path / "workspaces")
    return TestClient(create_app(cfg))


def _poll(client, run_id):
    for _ in range(60):
        d = client.get(f"/api/runs/{run_id}").json()
        if d["status"] in ("done", "error"):
            return d
        time.sleep(0.05)
    return client.get(f"/api/runs/{run_id}").json()


def test_doctor_endpoint_ok_and_no_key(tmp_path):
    client = _client(tmp_path)
    d = client.get("/api/doctor").json()
    assert d["ok"] is True
    assert "key" not in str(d).lower() or "api_key" not in str(d)


def test_fresh_clone_browser_only_path(tmp_path):
    """HEADLINE: configure + answer entirely through the API — no hand file edits."""
    client = _client(tmp_path)

    # 1. Create a workspace.
    ws = client.post("/api/workspaces", json={"name": "Acme — SOC 2"}).json()
    wid = ws["id"]
    assert wid == "acme-soc-2"

    # 2. Upload a KB document.
    r = client.post(f"/api/workspaces/{wid}/kb",
                    files={"files": ("incident.md", INCIDENT_MD, "text/markdown")})
    assert "incident.md" in [f["name"] for f in r.json()["files"]]

    # 3. Add an approved Q&A pair (optional).
    client.post(f"/api/workspaces/{wid}/qa",
                json={"question": "Do you encrypt data at rest?",
                      "answer": "Yes, AES-256 at rest.", "tags": ["soc2"]})

    # 4. Upload an evidence file.
    with open(FIX / "evidence" / "SOC2_Type_II_Report_v2.md", "rb") as fh:
        client.post(f"/api/workspaces/{wid}/evidence",
                    files={"files": ("SOC2_Type_II_Report_v2.md", fh.read(), "text/markdown")})

    # 5. Upload a questionnaire and run.
    with open(FIX / "sample.xlsx", "rb") as fh:
        run = client.post(f"/api/workspaces/{wid}/runs",
                          files={"questionnaire": ("sample.xlsx", fh.read())},
                          data={"tags": "soc2"}).json()
    run_id = run["run_id"]
    data = _poll(client, run_id)
    assert data["status"] == "done", data

    # 6. Accept a generated (non-attachment) item -> trains the workspace qa.yaml.
    gen = next(r for r in data["results"]
               if r["answer_type"] != "attachment" and "incident" in r["question_text"].lower())
    acc = client.post(f"/api/runs/{run_id}/items/{gen['question_id']}/accept",
                      json={"answer": "Yes — documented IR plan, reviewed annually."}).json()
    assert acc["trained"] is True

    lib = AnswerLibrary.load(Path(tmp_path) / "workspaces" / wid / "qa.yaml")
    assert any("incident" in e.question.lower() for e in lib.entries)
    # The pre-seeded encryption pair is still there too.
    assert any("encrypt" in e.question.lower() for e in lib.entries)

    # 7. Export.
    exp = client.post(f"/api/runs/{run_id}/export").json()
    assert "answered.xlsx" in exp["artifacts"].values()
    assert "results.json" in exp["artifacts"].values()


def test_workspaces_are_isolated_and_deletable(tmp_path):
    client = _client(tmp_path)
    a = client.post("/api/workspaces", json={"name": "Client A"}).json()["id"]
    b = client.post("/api/workspaces", json={"name": "Client B"}).json()["id"]
    client.post(f"/api/workspaces/{a}/kb", files={"files": ("a.md", "A only", "text/markdown")})
    client.post(f"/api/workspaces/{b}/kb", files={"files": ("b.md", "B only", "text/markdown")})

    a_files = [f["name"] for f in client.get(f"/api/workspaces/{a}/kb").json()["files"]]
    b_files = [f["name"] for f in client.get(f"/api/workspaces/{b}/kb").json()["files"]]
    assert a_files == ["a.md"] and b_files == ["b.md"]

    ids = [w["id"] for w in client.get("/api/workspaces").json()]
    assert set(ids) == {a, b}

    client.delete(f"/api/workspaces/{a}")
    assert not (Path(tmp_path) / "workspaces" / a).exists()
    assert [w["id"] for w in client.get("/api/workspaces").json()] == [b]


def test_upload_validation_rejects_bad_type(tmp_path):
    """Phase 8 C: bulk upload rejects unsupported files PER FILE (no abort)."""
    client = _client(tmp_path)
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    r = client.post(
        f"/api/workspaces/{wid}/kb",
        files=[("files", ("ok.md", b"policy text", "text/markdown")),
               ("files", ("evil.exe", b"MZ", "application/octet-stream"))],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == ["ok.md"]                       # good one ingested
    assert any(".exe" in x["reason"] for x in body["rejected"])  # bad one rejected w/ reason


def test_tags_persist_and_scope(tmp_path):
    client = _client(tmp_path)
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    client.post(f"/api/workspaces/{wid}/kb", files={"files": ("enc.md", "AES-256 at rest.", "text/markdown")})
    r = client.patch(f"/api/workspaces/{wid}/kb/enc.md", json={"tags": ["gdpr"]})
    files = {f["name"]: f["tags"] for f in r.json()["files"]}
    assert files["enc.md"] == ["gdpr"]
    # Sidecar persisted on disk.
    from qresponder.kb.tags import load_tag_sidecar
    assert load_tag_sidecar(Path(tmp_path) / "workspaces" / wid / "kb")["enc.md"] == ["gdpr"]


def test_settings_reject_provider_key_fields(tmp_path):
    client = _client(tmp_path)
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    ok = client.patch(f"/api/workspaces/{wid}/settings", json={"kb_mode": "retrieval", "detect_conflicts": False})
    assert ok.status_code == 200
    assert ok.json()["settings"]["kb_mode"] == "retrieval"
    bad = client.patch(f"/api/workspaces/{wid}/settings", json={"anthropic_api_key": "sk-secret"})
    assert bad.status_code == 400


def test_no_endpoint_returns_key(tmp_path):
    # Even with a key configured, it must never appear in any response.
    cfg = Config(llm_provider="anthropic", anthropic_api_key="sk-ant-SECRET-123", kb_mode="in_context")
    cfg.extra["workspaces_dir"] = str(tmp_path / "ws")
    client = TestClient(create_app(cfg))
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    bodies = [
        client.get("/api/status").text,
        client.get(f"/api/workspaces/{wid}").text,
        client.get(f"/api/workspaces/{wid}/settings").text,
    ]
    for b in bodies:
        assert "SECRET" not in b


def test_workspace_batch(tmp_path):
    """Part D: a workspace batch processes multiple files and returns a zip."""
    client = _client(tmp_path)
    wid = client.post("/api/workspaces", json={"name": "Acme"}).json()["id"]
    client.post(f"/api/workspaces/{wid}/kb",
                files={"files": ("incident.md", INCIDENT_MD, "text/markdown")})
    with open(FIX / "sample.xlsx", "rb") as fh:
        data = fh.read()
    resp = client.post(
        f"/api/workspaces/{wid}/batch",
        files=[("files", ("q1.xlsx", data, "application/octet-stream")),
               ("files", ("q2.xlsx", data, "application/octet-stream"))],
    )
    body = resp.json()
    assert body["summary"]["n_files"] == 2
    assert body["summary"]["succeeded"] == 2
    # The zip is downloadable via the shared download route.
    dl = client.get(body["download"])
    assert dl.status_code == 200
    assert dl.content[:2] == b"PK"  # zip magic


def test_bulk_qa_import_endpoint(tmp_path):
    client = _client(tmp_path)
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    csv_bytes = b"question,answer\nDo you encrypt at rest?,Yes AES-256.\n"
    res = client.post(
        f"/api/workspaces/{wid}/qa/import",
        files=[("files", ("pairs.csv", csv_bytes, "text/csv")),
               ("files", ("bad.pdf", b"%PDF", "application/pdf"))],
    ).json()
    assert res["imported"] == 1
    assert any("bad.pdf" == r["name"] for r in res["rejected"])
    assert res["total"] == 1


def test_kb_check_endpoint(tmp_path):
    client = _client(tmp_path)
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    # Distinct entries (the qa POST dedups near-duplicates on add).
    client.post(f"/api/workspaces/{wid}/qa", json={"question": "Do you support SSO?", "answer": "Yes."})
    client.post(f"/api/workspaces/{wid}/qa", json={"question": "Do you encrypt at rest?", "answer": "Yes, AES-256."})
    report = client.get(f"/api/workspaces/{wid}/kb-check").json()
    assert report["n_entries"] == 2
    assert report["clean"] is True  # endpoint returns a well-formed report


def test_qa_crud(tmp_path):
    client = _client(tmp_path)
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    client.post(f"/api/workspaces/{wid}/qa", json={"question": "Q1?", "answer": "A1", "tags": ["soc2"]})
    entries = client.get(f"/api/workspaces/{wid}/qa").json()["entries"]
    assert len(entries) == 1 and entries[0]["answer"] == "A1"
    client.put(f"/api/workspaces/{wid}/qa/0", json={"answer": "A1-edited"})
    assert client.get(f"/api/workspaces/{wid}/qa").json()["entries"][0]["answer"] == "A1-edited"
    client.delete(f"/api/workspaces/{wid}/qa/0")
    assert client.get(f"/api/workspaces/{wid}/qa").json()["entries"] == []
