"""Confluence connector (Phase 12) — extras-gated, injectable client, offline-tested.

Fetches pages from one user-specified Confluence space using the user's own API
token (server-side config, never the browser), and ingests them via the existing
bulk path. The base URL is SSRF-guarded; the real client is lazy-imported so the
slim image and import-guard hold. Runs only on explicit `connect confluence`.
"""

from __future__ import annotations

from .base import ConnectorError, TokenConnector


def _cloud_get(cloud_id: str, token: str, path: str, timeout: int = 15):  # pragma: no cover - real network
    import json
    import urllib.request

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/rest/api{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def list_spaces(token: str, cloud_id: str, fetch=None, max_spaces: int = 500) -> list[dict]:
    """List the spaces the signed-in user can see: [{key, name}]. `fetch(cloud_id,
    token, path) -> dict` is injectable so tests stay offline."""
    fetch = fetch or _cloud_get
    spaces, start, limit = [], 0, 50
    while len(spaces) < max_spaces:
        data = fetch(cloud_id, token, f"/space?limit={limit}&start={start}")
        results = data.get("results", [])
        for s in results:
            spaces.append({"key": s.get("key"), "name": s.get("name") or s.get("key")})
        if len(results) < limit:
            break
        start += limit
    return spaces


class ConfluenceConnector(TokenConnector):
    service = "Confluence"
    env_hint = "set confluence_token (+ confluence_base_url, confluence_email) in .env"
    default_ext = ".html"  # Confluence storage format is HTML; the KB loader reads it

    def __init__(self, space_key: str, token=None, base_url=None, email=None, cloud_id=None,
                 tags=None, client=None, timeout: int = 15, max_items: int = 200):
        super().__init__(space_key, token=token, base_url=base_url, tags=tags,
                         client=client, timeout=timeout, max_items=max_items)
        self.email = email
        self.cloud_id = cloud_id  # set → use the OAuth (3LO) Bearer + Cloud API path

    def _make_client(self):  # pragma: no cover - real network/SDK path
        if self.cloud_id:
            return self._oauth_client()
        if not self.base_url:
            raise ConnectorError("Confluence: sign in with Confluence, or set confluence_base_url in .env.")
        try:
            from atlassian import Confluence  # type: ignore
        except ImportError as exc:
            raise ConnectorError(
                'Confluence needs the optional extra: pip install "qresponder[connectors]".'
            ) from exc
        api = Confluence(url=self.base_url, username=self.email, password=self.token, timeout=self.timeout)

        def _client(space_key: str):
            docs, start, limit = [], 0, 50
            while len(docs) < self.max_items:
                batch = api.get_all_pages_from_space(space_key, start=start, limit=limit,
                                                     expand="body.storage")
                if not batch:
                    break
                for p in batch:
                    docs.append({"name": p.get("title"), "text": (p.get("body", {}).get("storage", {}) or {}).get("value", ""),
                                 "url": f"{self.base_url}/pages/{p.get('id')}"})
                if len(batch) < limit:
                    break
                start += limit
            return docs

        return _client

    def _oauth_client(self):  # pragma: no cover - real network path
        """OAuth 2.0 (3LO): Bearer token against the Atlassian Cloud REST API."""
        import json
        import urllib.parse
        import urllib.request

        base = f"https://api.atlassian.com/ex/confluence/{self.cloud_id}/rest/api"
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

        def _client(space_key: str):
            docs, start, limit = [], 0, 50
            while len(docs) < self.max_items:
                qs = urllib.parse.urlencode({"spaceKey": space_key, "expand": "body.storage",
                                             "limit": limit, "start": start})
                req = urllib.request.Request(f"{base}/content?{qs}", headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    data = json.loads(resp.read().decode("utf-8"))
                results = data.get("results", [])
                for p in results:
                    docs.append({"name": p.get("title"),
                                 "text": (p.get("body", {}).get("storage", {}) or {}).get("value", ""),
                                 "url": (p.get("_links", {}) or {}).get("webui", "")})
                if len(results) < limit:
                    break
                start += limit
            return docs

        return _client
