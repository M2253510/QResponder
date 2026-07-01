"""SharePoint connector (Phase 12) — extras-gated, injectable client, offline-tested.

Fetches documents from one user-specified SharePoint site's document library via
Microsoft Graph, using the user's own token (server-side config, never the
browser), and ingests them via the existing bulk path. The site URL is
SSRF-guarded; the real client is lazy-imported. Runs only on explicit
`connect sharepoint`.
"""

from __future__ import annotations

from .base import ConnectorError, TokenConnector


class SharePointConnector(TokenConnector):
    service = "SharePoint"
    env_hint = "set microsoft_token in .env"
    default_ext = ".txt"

    def _make_client(self):  # pragma: no cover - real network/SDK path
        try:
            import requests  # type: ignore
        except ImportError as exc:
            raise ConnectorError(
                'SharePoint needs the optional extra: pip install "qresponder[connectors]".'
            ) from exc
        headers = {"Authorization": f"Bearer {self.token}"}
        graph = "https://graph.microsoft.com/v1.0"

        def _client(site_id: str):
            docs = []
            url = f"{graph}/sites/{site_id}/drive/root/children"
            while url and len(docs) < self.max_items:
                r = requests.get(url, headers=headers, timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
                for item in data.get("value", []):
                    if "file" not in item:
                        continue
                    dl = item.get("@microsoft.graph.downloadUrl")
                    text = requests.get(dl, timeout=self.timeout).text if dl else ""
                    docs.append({"name": item.get("name"), "text": text, "url": item.get("webUrl")})
                url = data.get("@odata.nextLink")
            return docs

        return _client
