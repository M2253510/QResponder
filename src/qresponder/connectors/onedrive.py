"""OneDrive connector (Phase 12) — extras-gated, injectable client, offline-tested.

Fetches documents from one user-specified OneDrive folder via Microsoft Graph,
using the user's own token (server-side config, never the browser), and ingests
them via the existing bulk path. The real client is lazy-imported so the slim
image and import-guard hold. Runs only on explicit `connect onedrive`.
"""

from __future__ import annotations

from .base import ConnectorError, TokenConnector


class OneDriveConnector(TokenConnector):
    service = "OneDrive"
    env_hint = "set microsoft_token in .env"
    default_ext = ".txt"

    def _make_client(self):  # pragma: no cover - real network/SDK path
        try:
            import requests  # type: ignore
        except ImportError as exc:
            raise ConnectorError(
                'OneDrive needs the optional extra: pip install "qresponder[connectors]".'
            ) from exc
        headers = {"Authorization": f"Bearer {self.token}"}
        graph = "https://graph.microsoft.com/v1.0"

        def _client(folder_path: str):
            docs = []
            seg = f":/{folder_path.strip('/')}:" if folder_path.strip("/") else ""
            url = f"{graph}/me/drive/root{seg}/children"
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
