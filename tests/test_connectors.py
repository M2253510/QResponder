"""Source connector tests (Phase 10 B). All offline (injected fetcher)."""

from pathlib import Path

import pytest

from qresponder.connectors.base import ConnectorError, ingest_connector
from qresponder.connectors.folder import FolderConnector
from qresponder.connectors.website import WebsiteConnector, ssrf_ok
from qresponder.kb.in_context import InContextKB
from qresponder.kb.tags import load_tag_sidecar


def test_folder_import_ingests_with_tags_and_is_answerable(tmp_path):
    src = tmp_path / "docs"
    src.mkdir()
    (src / "policy.md").write_text("Data at rest is encrypted with AES-256.", encoding="utf-8")
    (src / "notes.txt").write_text("MFA is enforced.", encoding="utf-8")
    (src / "ignore.exe").write_bytes(b"MZ")
    kb = tmp_path / "kb"
    res = ingest_connector(FolderConnector(str(src), tags=["soc2"]), kb, tags=["soc2"])
    assert set(res["accepted"]) == {"policy.md", "notes.txt"}
    assert any("ignore.exe" in r["name"] for r in res["rejected"])
    assert load_tag_sidecar(kb)["policy.md"] == ["soc2"]
    # Provenance recorded.
    via = {f["name"]: f["via"] for f in res["files"]}
    assert via["policy.md"] == "upload"
    # Ingested docs are answerable.
    ctx = InContextKB.load(kb).assemble_context(scope_tags=["soc2"])
    assert "AES-256" in ctx


def test_folder_traversal_contained(tmp_path):
    src = tmp_path / "docs"
    src.mkdir()
    (src / "a.md").write_text("x", encoding="utf-8")
    # A sibling file outside the folder must never be ingested.
    (tmp_path / "secret.md").write_text("secret", encoding="utf-8")
    kb = tmp_path / "kb"
    res = ingest_connector(FolderConnector(str(src), tags=["t"]), kb)
    assert res["accepted"] == ["a.md"]
    assert not (kb / "secret.md").exists()


def test_ssrf_guard_rejects_local_and_private():
    assert not ssrf_ok("http://localhost/x")
    assert not ssrf_ok("http://127.0.0.1/x")
    assert not ssrf_ok("http://169.254.169.254/latest/meta-data/")
    assert not ssrf_ok("http://10.0.0.5/x")
    assert not ssrf_ok("file:///etc/passwd")
    assert ssrf_ok("https://example.com/x")
    assert ssrf_ok("http://127.0.0.1/x", allow_private=True)  # explicit override


def test_website_crawler_bounded_and_offline(tmp_path):
    pages = {
        "https://example.com/": '<html><body>Encryption at rest AES-256. <a href="/p2">next</a> <a href="https://other.com/x">ext</a></body></html>',
        "https://example.com/p2": "<html><body>MFA is enforced for all employees.</body></html>",
        "https://example.com/p3": "<html><body>should not be fetched (max_pages)</body></html>",
    }
    fetched = []

    def fake(url):
        fetched.append(url)
        return pages.get(url, "<html></html>")

    conn = WebsiteConnector("https://example.com/", depth=1, max_pages=2, tags=["web"], fetch=fake)
    kb = tmp_path / "kb"
    res = ingest_connector(conn, kb, tags=["web"])
    # Bounded to max_pages=2; never left the domain (other.com not fetched).
    assert len(res["accepted"]) == 2
    assert "https://other.com/x" not in fetched
    # Ingested with the 'web' tag.
    sidecar = load_tag_sidecar(kb)
    assert all(t == ["web"] for t in sidecar.values())
    assert "AES-256" in InContextKB.load(kb).assemble_context()


def test_website_seed_localhost_blocked():
    with pytest.raises(ConnectorError):
        WebsiteConnector("http://localhost:8000/", fetch=lambda u: "<html></html>").fetch()


def test_no_fetch_during_answering(monkeypatch):
    """Connectors must never run during the answering path."""
    import qresponder.connectors.website as web

    def boom(*a, **k):
        raise AssertionError("connector fetched during answering")

    monkeypatch.setattr(web, "_default_fetch", boom)
    from qresponder.config import Config
    from qresponder.core.pipeline import run_ask
    from qresponder.llm.mock import MockProvider

    fix = Path(__file__).parent / "fixtures"
    r = run_ask("Do you encrypt data at rest?", str(fix / "kb"), str(fix / "qa.yaml"),
                Config(llm_provider="mock", kb_mode="in_context"), scope_tags=["soc2"],
                provider=MockProvider())
    assert r is not None  # answered without any connector fetch
