"""Connector hardening (Phase 16) — every SaaS connector's real fetch/pagination
runs OFFLINE against real-API-shaped mocks (injected HTTP), plus OAuth refresh and
Microsoft wiring. No token/secret ever appears in a response."""

from urllib.parse import parse_qs, urlparse

import pytest

from qresponder.connectors.gdrive import GoogleDriveConnector
from qresponder.connectors.notion import NotionConnector
from qresponder.connectors.oauth import OAUTH_SPECS, authorize_url, refresh_access_token
from qresponder.connectors.onedrive import OneDriveConnector
from qresponder.connectors.sharepoint import SharePointConnector


# ---- Notion: databases.query + block children, start_cursor pagination ----

def test_notion_paginates_database_and_blocks():
    def http(method, url, headers=None, body=None, want="json"):
        assert headers.get("Notion-Version")  # real header present
        if url.endswith("/query"):  # database query (2 pages)
            if not body.get("start_cursor"):
                return {"results": [{"id": "p1", "url": "u1", "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "Backup Policy"}]}}}],
                    "has_more": True, "next_cursor": "c2"}
            return {"results": [{"id": "p2", "url": "u2", "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Access Policy"}]}}}], "has_more": False}
        # block children
        pid = url.split("/blocks/")[1].split("/")[0]
        return {"results": [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": f"body of {pid}"}]}}],
                "has_more": False}
    docs = NotionConnector("db1", token="t", http=http).fetch()
    names = {d.source_name for d in docs}
    assert any("backup-policy" in n for n in names) and any("access-policy" in n for n in names)
    assert b"body of p1" in next(d.content for d in docs if "backup" in d.source_name)


# ---- Google Drive: files.list pageToken + export/get_media ----

def test_gdrive_paginates_and_exports():
    def http(method, url, headers=None, body=None, want="json"):
        if "/files?" in url:
            q = parse_qs(urlparse(url).query)
            if "pageToken" not in q:
                return {"files": [{"id": "d1", "name": "Doc One", "mimeType": "application/vnd.google-apps.document"}],
                        "nextPageToken": "tok2"}
            return {"files": [{"id": "d2", "name": "notes.txt", "mimeType": "text/plain"}]}
        if "/export" in url:
            return b"exported google doc text"
        return b"plain text file"
    docs = GoogleDriveConnector("folder123", token="t", http=http).fetch()
    assert {d.source_name for d in docs} == {"doc-one.txt", "notes.txt"}
    assert b"exported google doc text" in docs[0].content


# ---- Microsoft Graph (SharePoint + OneDrive): @odata.nextLink + downloadUrl ----

@pytest.mark.parametrize("cls,target", [(SharePointConnector, "site1"), (OneDriveConnector, "Policies")])
def test_graph_paginates_and_downloads(cls, target):
    pages = {}
    def http(method, url, headers=None, body=None, want="json"):
        if url.endswith("dl1") or url.endswith("dl2"):
            return b"file body for " + url.split("/")[-1].encode()
        if "nextpage" not in url and not pages.get("p1"):
            pages["p1"] = True
            return {"value": [{"name": "a.txt", "file": {"mimeType": "text/plain"}, "webUrl": "w1",
                               "@microsoft.graph.downloadUrl": "https://dl/dl1"}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/nextpage"}
        return {"value": [{"name": "b.md", "file": {"mimeType": "text/markdown"}, "webUrl": "w2",
                           "@microsoft.graph.downloadUrl": "https://dl/dl2"}]}
    docs = cls(target, token="t", http=http).fetch()
    assert {d.source_name for d in docs} == {"a.txt", "b.md"}
    assert docs[0].content.startswith(b"file body")


# ---- OAuth refresh (all providers) ----

def test_refresh_access_token_mints_new_and_keeps_refresh():
    seen = {}
    def fetch(url, data, headers):
        seen.update(data)
        return {"access_token": "new-at"}  # provider omits a new refresh token
    tok = refresh_access_token("gdrive", "old-rt", "cid", "sec", fetch=fetch)
    assert tok["access_token"] == "new-at"
    assert seen["grant_type"] == "refresh_token" and seen["refresh_token"] == "old-rt"


def test_microsoft_spec_and_authorize_url():
    assert "microsoft" in OAUTH_SPECS
    url = authorize_url("microsoft", "cid", "http://127.0.0.1:8000/api/oauth/callback", "st", "ch")
    q = parse_qs(urlparse(url).query)
    assert "Files.Read.All" in q["scope"][0] and "offline_access" in q["scope"][0]
    assert "login.microsoftonline.com" in url


# ---- web: refresh-on-401 during sync, no secret leak ----
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from qresponder.config import Config  # noqa: E402
from qresponder.web.app import create_app  # noqa: E402


def _client(tmp_path, **kw):
    cfg = Config(llm_provider="mock", kb_mode="in_context", **kw)
    cfg.extra["workspaces_dir"] = str(tmp_path / "ws")
    cfg.extra["oauth_dir"] = str(tmp_path / "oauth")
    app = create_app(cfg)
    return app, TestClient(app)


def test_sync_refreshes_token_on_401_then_succeeds(tmp_path):
    app, client = _client(tmp_path, notion_client_id="nid", notion_client_secret="sec")
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    # Sign in via OAuth (offline) so the connection has a refresh token stored.
    app.state.oauth_fetch = lambda url, data, headers: (
        {"access_token": "fresh-at"} if data.get("grant_type") == "refresh_token"
        else {"access_token": "at1", "refresh_token": "rt1"})
    start = client.get(f"/api/workspaces/{wid}/connections/notion/authorize").json()
    state = parse_qs(urlparse(start["authorize_url"]).query)["state"][0]
    client.get(f"/api/oauth/callback?code=abc&state={state}")
    cid = client.get(f"/api/workspaces/{wid}/connections").json()["connections"][0]["id"]
    client.patch(f"/api/workspaces/{wid}/connections/{cid}", json={"config": {"database": "db1"}})

    # Injected connector client: raise 401 on the first call, succeed after refresh.
    calls = {"n": 0}
    def flaky(target):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("401 Unauthorized")
        return [{"name": "policy", "text": "AES-256 at rest."}]
    app.state.connector_client = flaky
    r = client.post(f"/api/workspaces/{wid}/connections/{cid}/sync")
    assert r.status_code == 200 and r.json()["ingested"] == 1  # refreshed + retried
    assert calls["n"] == 2
    # The refreshed token was persisted server-side, never returned to the browser.
    assert "fresh-at" not in client.get(f"/api/workspaces/{wid}/connections").text
