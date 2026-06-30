"""Ask mode tests (Phase 10 A). Same grounded path on one question. Offline."""

from pathlib import Path

import pytest

from qresponder.config import Config
from qresponder.core.pipeline import run_ask
from qresponder.llm.mock import MockProvider
from qresponder.models import Status

FIX = Path(__file__).parent / "fixtures"


def test_ask_grounded_answer_with_citation_and_audit():
    cfg = Config(llm_provider="mock", kb_mode="in_context")
    r = run_ask("Do you have a documented incident response plan?",
                str(FIX / "kb"), str(FIX / "qa.yaml"), cfg, scope_tags=["soc2"],
                provider=MockProvider())
    assert r.status == Status.ANSWERED
    assert r.confidence.value in ("high", "medium")
    assert r.citations
    # Same AuditTrail structure as the questionnaire path.
    assert r.audit is not None
    assert r.audit.cited and r.audit.confidence_rationale


def test_ask_unsupported_abstains_no_fabrication():
    cfg = Config(llm_provider="mock", kb_mode="in_context")
    r = run_ask("What is your office lease expiration date in Tokyo?",
                str(FIX / "kb"), str(FIX / "qa.yaml"), cfg, scope_tags=["soc2"],
                provider=MockProvider())
    assert r.status == Status.NEEDS_REVIEW
    assert r.missing_info
    assert "tokyo" not in (r.answer or "").lower()


# --- web ask ---
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from qresponder.web.app import create_app  # noqa: E402

INCIDENT_MD = "Tags: soc2\n\nWe maintain a documented incident response plan, reviewed annually."


def _client(tmp_path):
    cfg = Config(llm_provider="mock", kb_mode="in_context")
    cfg.extra["workspaces_dir"] = str(tmp_path / "ws")
    return TestClient(create_app(cfg))


def test_web_ask_returns_grounded_answer_no_key(tmp_path):
    cfg = Config(llm_provider="mock", kb_mode="in_context", anthropic_api_key="sk-SECRET")
    cfg.extra["workspaces_dir"] = str(tmp_path / "ws")
    client = TestClient(create_app(cfg))
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    client.post(f"/api/workspaces/{wid}/kb",
                files=[("files", ("incident.md", INCIDENT_MD, "text/markdown"))])
    r = client.post(f"/api/workspaces/{wid}/ask",
                    json={"question": "Do you have an incident response plan?", "tags": "soc2"})
    body = r.json()
    assert body["status"] == "answered"
    assert body["citations"]
    assert body["audit"]["confidence_rationale"]
    assert "SECRET" not in r.text  # key never returned
