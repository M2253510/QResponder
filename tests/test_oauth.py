"""OAuth connector-login tests (Phase 12). Fully offline — the token exchange is
injected, so no provider network is touched. Verifies the flow keeps the client
secret and access token server-side."""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from qresponder.connectors.oauth import (OAUTH_SPECS, TokenStore, authorize_url,
                                         exchange_code, make_pkce, make_state)


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = make_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected
    assert make_state() != make_state()  # unique


def test_authorize_url_carries_pkce_and_scopes():
    url = authorize_url("gdrive", "client-abc", "http://127.0.0.1:8000/api/oauth/callback", "st8", "chal")
    q = parse_qs(urlparse(url).query)
    assert q["response_type"] == ["code"] and q["client_id"] == ["client-abc"]
    assert q["code_challenge"] == ["chal"] and q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["st8"]
    assert "drive.readonly" in q["scope"][0]  # Google scope present
    # no secret ever appears in an authorize URL
    assert "secret" not in url.lower()


def test_exchange_code_uses_injected_fetch_and_returns_token():
    seen = {}
    def fake_fetch(url, data, headers):
        seen["url"] = url; seen["data"] = data; seen["headers"] = headers
        return {"access_token": "at-123", "refresh_token": "rt-456", "token_type": "bearer"}
    tok = exchange_code("gdrive", "the-code", "cid", "the-secret",
                        "http://127.0.0.1:8000/api/oauth/callback", "verifier-xyz", fetch=fake_fetch)
    assert tok["access_token"] == "at-123"
    assert seen["url"] == OAUTH_SPECS["gdrive"]["token_url"]
    assert seen["data"]["code_verifier"] == "verifier-xyz"
    assert seen["data"]["client_secret"] == "the-secret"  # google uses POST body auth


def test_notion_exchange_uses_basic_client_auth():
    seen = {}
    def fake_fetch(url, data, headers):
        seen.update(headers=headers, data=data); return {"access_token": "at"}
    exchange_code("notion", "c", "cid", "sec", "http://127.0.0.1:8000/api/oauth/callback", "v", fetch=fake_fetch)
    assert seen["headers"]["Authorization"].startswith("Basic ")
    assert "client_secret" not in seen["data"]  # secret is in the Basic header, not the body


def test_token_store_roundtrip(tmp_path):
    store = TokenStore(tmp_path / ".oauth")
    assert store.has("notion") is False
    store.save("notion", {"access_token": "abc"})
    assert store.has("notion") and store.access_token("notion") == "abc"
    store.forget("notion")
    assert store.has("notion") is False


# --- web flow (offline; injected token exchange) ---
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from qresponder.config import Config  # noqa: E402
from qresponder.web.app import create_app  # noqa: E402


def _client(tmp_path, **cfgkw):
    cfg = Config(llm_provider="mock", kb_mode="in_context", **cfgkw)
    cfg.extra["workspaces_dir"] = str(tmp_path / "ws")
    cfg.extra["oauth_dir"] = str(tmp_path / "oauth")
    app = create_app(cfg)
    return app, TestClient(app)


def test_oauth_status_reports_configured_without_secret(tmp_path):
    _app, client = _client(tmp_path, notion_client_id="nid", notion_client_secret="super-secret")
    rows = {r["provider"]: r for r in client.get("/api/oauth/status").json()}
    assert rows["notion"]["configured"] is True and rows["notion"]["connected"] is False
    assert rows["gdrive"]["configured"] is False
    assert "super-secret" not in client.get("/api/oauth/status").text  # secret never leaks


def test_oauth_start_requires_configured_app(tmp_path):
    _app, client = _client(tmp_path)  # no client creds
    r = client.get("/api/oauth/notion/start")
    assert r.status_code == 400 and "not configured" in r.json()["detail"].lower()


def test_oauth_full_flow_stores_token_server_side(tmp_path):
    app, client = _client(tmp_path, notion_client_id="nid", notion_client_secret="sec")
    # Inject the token exchange so no network is used.
    app.state.oauth_fetch = lambda url, data, headers: {"access_token": "notion-oauth-tok"}
    start = client.get("/api/oauth/notion/start").json()
    state = parse_qs(urlparse(start["authorize_url"]).query)["state"][0]
    cb = client.get(f"/api/oauth/callback?code=abc&state={state}")
    assert cb.status_code == 200 and "Connected" in cb.text
    # Now marked connected — but the token is never returned to the browser.
    rows = {r["provider"]: r for r in client.get("/api/oauth/status").json()}
    assert rows["notion"]["connected"] is True
    assert "notion-oauth-tok" not in client.get("/api/connectors").text


def test_oauth_callback_rejects_bad_state(tmp_path):
    app, client = _client(tmp_path, notion_client_id="nid", notion_client_secret="sec")
    app.state.oauth_fetch = lambda url, data, headers: {"access_token": "x"}
    cb = client.get("/api/oauth/callback?code=abc&state=forged-state")
    assert cb.status_code == 200 and "invalid" in cb.text.lower()  # CSRF state check


def test_connect_uses_stored_oauth_token(tmp_path, monkeypatch):
    """After sign-in, connect() should pull the OAuth token from the server store and
    ingest — verified with an injected connector client (still offline)."""
    app, client = _client(tmp_path, notion_client_id="nid", notion_client_secret="sec")
    app.state.oauth_fetch = lambda url, data, headers: {"access_token": "stored-tok"}
    wid = client.post("/api/workspaces", json={"name": "W"}).json()["id"]
    start = client.get("/api/oauth/notion/start").json()
    state = parse_qs(urlparse(start["authorize_url"]).query)["state"][0]
    client.get(f"/api/oauth/callback?code=abc&state={state}")

    # Patch the Notion connector to use an injected client and record the token it got.
    import qresponder.connectors.notion as nmod
    captured = {}
    orig_init = nmod.NotionConnector.__init__
    def spy_init(self, target, token=None, **kw):
        captured["token"] = token
        orig_init(self, target, token=token, client=lambda t: [{"name": "policy", "text": "AES-256 at rest."}], **kw)
    monkeypatch.setattr(nmod.NotionConnector, "__init__", spy_init)

    r = client.post(f"/api/workspaces/{wid}/connect", json={"type": "notion", "database": "db1", "tags": ["soc2"]})
    assert r.status_code == 200 and len(r.json()["accepted"]) == 1
    assert captured["token"] == "stored-tok"  # the OAuth token was used, not a .env token
