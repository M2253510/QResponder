"""SaaS connector tests (Phase 12) — Confluence / Notion / SharePoint / OneDrive.
All offline: the SaaS client is injected, so no network and no SDK are needed.
"""

from pathlib import Path

import pytest

from qresponder.connectors.base import ConnectorError, ingest_connector
from qresponder.connectors.confluence import ConfluenceConnector
from qresponder.connectors.notion import NotionConnector
from qresponder.connectors.onedrive import OneDriveConnector
from qresponder.connectors.sharepoint import SharePointConnector
from qresponder.kb.in_context import InContextKB
from qresponder.kb.tags import load_tag_sidecar


def _fake(docs):
    """An injected client: callable(target) -> list[dict], ignoring the target."""
    return lambda target: docs


ALL = [
    (ConfluenceConnector, {"base_url": "https://acme.atlassian.net"}),
    (NotionConnector, {}),
    (SharePointConnector, {}),
    (OneDriveConnector, {}),
]


@pytest.mark.parametrize("cls,extra", ALL)
def test_connector_ingests_with_provenance_and_tags(tmp_path, cls, extra):
    client = _fake([
        {"name": "Security Policy", "text": "Data at rest is encrypted with AES-256.", "url": "https://x/1"},
        {"title": "Access", "content": "MFA is enforced for all admins.", "url": "https://x/2"},
        {"name": "Empty", "text": ""},  # empty doc is skipped
    ])
    kb = tmp_path / "kb"
    conn = cls("target", token="tok", client=client, tags=["soc2"], **extra)
    res = ingest_connector(conn, kb, tags=["soc2"])
    assert len(res["accepted"]) == 2  # the empty doc dropped
    # Provenance sidecar carries the tag on every ingested doc.
    sidecar = load_tag_sidecar(kb)
    assert all(v == ["soc2"] for v in sidecar.values())
    # The ingested content is answerable through the normal grounded path.
    ctx = InContextKB.load(kb).assemble_context(scope_tags=["soc2"])
    assert "AES-256" in ctx and "MFA" in ctx


@pytest.mark.parametrize("cls,extra", ALL)
def test_missing_credential_is_clear_error_not_crash(cls, extra):
    conn = cls("target", token="", client=None, **extra)  # no token, no injected client
    with pytest.raises(ConnectorError) as ei:
        conn.fetch()
    assert "missing credential" in str(ei.value).lower()


def test_confluence_ssrf_guard_blocks_private_base_url():
    conn = ConfluenceConnector("SPACE", token="tok", base_url="http://169.254.169.254/wiki",
                               client=_fake([{"name": "x", "text": "y"}]))
    with pytest.raises(ConnectorError) as ei:
        conn.fetch()
    assert "ssrf" in str(ei.value).lower()


def test_connector_respects_max_items():
    docs = [{"name": f"doc{i}", "text": f"body {i}"} for i in range(10)]
    conn = NotionConnector("db", token="tok", client=_fake(docs), max_items=3)
    assert len(conn.fetch()) == 3


def test_answering_path_never_triggers_a_connector(tmp_path, monkeypatch):
    """Guardrail: run_ask must not construct/fetch any connector. We trip a fuse if
    TokenConnector.fetch is ever called during answering."""
    import qresponder.connectors.base as base
    from qresponder.config import Config
    from qresponder.core.pipeline import run_ask
    from qresponder.llm.mock import MockProvider

    tripped = {"n": 0}
    orig = base.TokenConnector.fetch
    monkeypatch.setattr(base.TokenConnector, "fetch",
                        lambda self: (tripped.__setitem__("n", tripped["n"] + 1), orig(self))[1])
    kb = tmp_path / "kb"; kb.mkdir()
    (kb / "p.md").write_text("Tags: soc2\n\nData at rest is encrypted with AES-256.", encoding="utf-8")
    cfg = Config(llm_provider="mock", kb_mode="in_context")
    run_ask("Do you encrypt data at rest?", str(kb), str(tmp_path / "qa.yaml"), cfg,
            scope_tags=["soc2"], provider=MockProvider())
    assert tripped["n"] == 0  # answering never fetched a connector


# --- web: connectors listing never leaks credentials ---
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from qresponder.config import Config  # noqa: E402
from qresponder.web.app import create_app  # noqa: E402


def test_web_connectors_list_reports_configured_without_leaking_token(tmp_path):
    cfg = Config(llm_provider="mock", kb_mode="in_context",
                 notion_token="secret-notion-xyz", microsoft_token="secret-ms-abc")
    cfg.extra["workspaces_dir"] = str(tmp_path / "ws")
    client = TestClient(create_app(cfg))
    r = client.get("/api/connectors")
    assert r.status_code == 200
    by_type = {c["type"]: c for c in r.json()}
    for t in ("folder", "website", "confluence", "notion", "sharepoint", "onedrive", "gdrive"):
        assert t in by_type
    assert by_type["notion"]["configured"] is True
    assert by_type["confluence"]["configured"] is False  # no confluence token set
    # The credential itself never appears in the response.
    assert "secret-notion-xyz" not in r.text and "secret-ms-abc" not in r.text
