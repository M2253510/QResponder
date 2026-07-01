"""Confluence connector (Phase 12) — extras-gated, injectable client, offline-tested.

Fetches pages from one user-specified Confluence space using the user's own API
token (server-side config, never the browser), and ingests them via the existing
bulk path. The base URL is SSRF-guarded; the real client is lazy-imported so the
slim image and import-guard hold. Runs only on explicit `connect confluence`.
"""

from __future__ import annotations

from .base import ConnectorError, TokenConnector


class ConfluenceConnector(TokenConnector):
    service = "Confluence"
    env_hint = "set confluence_token (+ confluence_base_url, confluence_email) in .env"
    default_ext = ".html"  # Confluence storage format is HTML; the KB loader reads it

    def __init__(self, space_key: str, token=None, base_url=None, email=None, tags=None,
                 client=None, timeout: int = 15, max_items: int = 200):
        super().__init__(space_key, token=token, base_url=base_url, tags=tags,
                         client=client, timeout=timeout, max_items=max_items)
        self.email = email

    def _make_client(self):  # pragma: no cover - real network/SDK path
        if not self.base_url:
            raise ConnectorError("Confluence: confluence_base_url is required.")
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
