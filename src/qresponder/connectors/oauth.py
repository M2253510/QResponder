"""OAuth 2.0 (Authorization Code + PKCE) for the source connectors (Phase 12).

Lets a user connect Notion / Google Drive / Confluence by clicking "Sign in" and
consenting in their own browser, instead of pasting a personal token. The flow is
standard Authorization Code + PKCE:

  1. /api/oauth/{provider}/start  → build the provider authorize URL (state + PKCE)
  2. user logs in + consents on the provider
  3. /api/oauth/callback          → exchange the code for a token, store it SERVER-SIDE

Boundaries: the OAuth **client secret never leaves the server**, and the resulting
**access token is never sent to the browser** — it's persisted server-side and used
only by an explicit `connect`. OAuth still needs a one-time registered OAuth app per
provider (client_id/secret in config); this module can't create that. The HTTP
exchange is injectable so the whole flow is testable offline.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from pathlib import Path
from urllib.parse import urlencode

# Per-provider OAuth endpoints + the config fields holding their client credentials.
OAUTH_SPECS: dict[str, dict] = {
    "notion": {
        "label": "Notion",
        "authorize_url": "https://api.notion.com/v1/oauth/authorize",
        "token_url": "https://api.notion.com/v1/oauth/token",
        "scopes": [],  # Notion grants at the integration level, not per-scope
        "extra_authorize": {"owner": "user"},
        "client_auth": "basic",  # token endpoint uses HTTP Basic client auth
        "id_field": "notion_client_id", "secret_field": "notion_client_secret",
    },
    "gdrive": {
        "label": "Google Drive",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/drive.readonly"],
        "extra_authorize": {"access_type": "offline", "prompt": "consent"},
        "client_auth": "post",
        "id_field": "google_client_id", "secret_field": "google_client_secret",
    },
    "confluence": {
        "label": "Confluence",
        "authorize_url": "https://auth.atlassian.com/authorize",
        "token_url": "https://auth.atlassian.com/oauth/token",
        # Granular scopes for the Confluence Cloud REST API v2 (classic v1 scopes are
        # retired). read:space + read:page cover listing spaces and reading page bodies.
        "scopes": ["read:space:confluence", "read:page:confluence", "offline_access"],
        "extra_authorize": {"audience": "api.atlassian.com", "prompt": "consent"},
        "client_auth": "post",
        "id_field": "confluence_client_id", "secret_field": "confluence_client_secret",
    },
    "microsoft": {
        "label": "Microsoft 365",
        "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": ["Files.Read.All", "Sites.Read.All", "offline_access"],
        "extra_authorize": {"prompt": "consent"},
        "client_auth": "post",
        "id_field": "microsoft_client_id", "secret_field": "microsoft_client_secret",
    },
}

# Connection type → the OAuth provider it authenticates through (SharePoint and
# OneDrive both ride Microsoft identity / Graph).
CONNECTOR_OAUTH = {
    "gdrive": "gdrive", "notion": "notion", "confluence": "confluence",
    "sharepoint": "microsoft", "onedrive": "microsoft",
}


class OAuthError(RuntimeError):
    pass


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) using S256 — RFC 7636."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def make_state() -> str:
    return secrets.token_urlsafe(24)


def client_credentials(config, provider: str) -> tuple[str, str]:
    spec = OAUTH_SPECS[provider]
    return (getattr(config, spec["id_field"], "") or "",
            getattr(config, spec["secret_field"], "") or "")


def is_configured(config, provider: str) -> bool:
    cid, secret = client_credentials(config, provider)
    return bool(cid and secret)


def authorize_url(provider: str, client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    spec = OAUTH_SPECS[provider]
    params = {
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    }
    if spec["scopes"]:
        params["scope"] = " ".join(spec["scopes"])
    params.update(spec.get("extra_authorize", {}))
    return spec["authorize_url"] + "?" + urlencode(params)


def _http_post_form(url: str, data: dict, headers: dict) -> dict:  # pragma: no cover - real network
    import urllib.request

    body = urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - explicit token endpoint
        return json.loads(resp.read().decode("utf-8"))


def exchange_code(provider: str, code: str, client_id: str, client_secret: str,
                  redirect_uri: str, verifier: str, fetch=None) -> dict:
    """Trade the auth code for a token dict. `fetch(url, data, headers)->dict` is
    injectable so tests never hit the network. Raises OAuthError on a provider error."""
    spec = OAUTH_SPECS[provider]
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "code_verifier": verifier, "client_id": client_id}
    headers: dict = {}
    if spec["client_auth"] == "basic":
        token = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    else:
        data["client_secret"] = client_secret
    resp = (fetch or _http_post_form)(spec["token_url"], data, headers)
    if not isinstance(resp, dict) or "access_token" not in resp:
        raise OAuthError(f"{spec['label']}: token exchange failed ({resp.get('error') if isinstance(resp, dict) else 'no token'}).")
    return resp


def refresh_access_token(provider: str, refresh_tok: str, client_id: str, client_secret: str,
                         fetch=None) -> dict:
    """Use a refresh token to mint a fresh access token so long-lived connections
    don't silently die. Providers may or may not rotate the refresh token — if the
    response omits one, the caller keeps the existing refresh_token. Server-side only."""
    spec = OAUTH_SPECS[provider]
    if not refresh_tok:
        raise OAuthError(f"{spec['label']}: no refresh token stored — reconnect.")
    data = {"grant_type": "refresh_token", "refresh_token": refresh_tok, "client_id": client_id}
    headers: dict = {}
    if spec["client_auth"] == "basic":
        token = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    else:
        data["client_secret"] = client_secret
    if spec["scopes"]:
        data["scope"] = " ".join(spec["scopes"])
    resp = (fetch or _http_post_form)(spec["token_url"], data, headers)
    if not isinstance(resp, dict) or "access_token" not in resp:
        raise OAuthError(f"{spec['label']}: token refresh failed.")
    return resp


def is_auth_error(exc: Exception) -> bool:
    """Heuristic: did a connector fetch fail because the access token expired (401)?
    Kept string-based so it works across urllib / requests / SDK error shapes."""
    code = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if code in (401, 403):
        return True
    s = str(exc).lower()
    return "401" in s or "unauthorized" in s or "invalid_grant" in s or "token expired" in s or "invalidauthenticationtoken" in s


def atlassian_cloud_id(access_token: str, fetch=None) -> str | None:  # pragma: no cover - real network
    """Resolve the Atlassian Cloud id for an OAuth token (needed to address the
    Confluence Cloud API at api.atlassian.com/ex/confluence/{cloudId})."""
    def _default(url, headers):
        import urllib.request

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))

    data = (fetch or _default)("https://api.atlassian.com/oauth/token/accessible-resources",
                              {"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
    if isinstance(data, list) and data:
        return data[0].get("id")
    return None


class TokenStore:
    """Persists OAuth tokens server-side, one file per provider. Never returned to
    the browser. Account-level (this is a local, single-user tool)."""

    def __init__(self, directory):
        self.dir = Path(directory)

    def _path(self, provider: str) -> Path:
        return self.dir / f"{provider}.json"

    def save(self, provider: str, token: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(provider).write_text(json.dumps(token), encoding="utf-8")

    def load(self, provider: str) -> dict | None:
        p = self._path(provider)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None

    def has(self, provider: str) -> bool:
        tok = self.load(provider)
        return bool(tok and tok.get("access_token"))

    def access_token(self, provider: str) -> str | None:
        tok = self.load(provider)
        return (tok or {}).get("access_token")

    def forget(self, provider: str) -> None:
        p = self._path(provider)
        if p.exists():
            p.unlink()
