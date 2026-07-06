"""SharePoint connector (Phase 12; hardened Phase 16) — Microsoft Graph, offline-tested.

Uses the user's Microsoft OAuth token (Files.Read.All / Sites.Read.All) against Graph
(https://graph.microsoft.com/v1.0): list drive items under the chosen site's default
drive, download textual files via /content. @odata.nextLink pagination to completion,
bounded by max_items. The HTTP fetcher is injectable so the real logic runs offline
against real-API-shaped mocks. Runs only on explicit connect/test/sync.
"""

from __future__ import annotations

from .base import TokenConnector, default_http

_GRAPH = "https://graph.microsoft.com/v1.0"
_TEXTUAL = ("text/", "application/rtf", "application/json", "application/xml", "text/html")


def graph_drive_client(http, headers, root_url: str, max_items: int):
    """Shared Graph drive walk (used by SharePoint + OneDrive). Pages @odata.nextLink,
    downloads textual items via their @microsoft.graph.downloadUrl or /content."""
    def _client(_target: str):
        docs, url = [], root_url
        while url and len(docs) < max_items:
            data = http("GET", url, headers=headers)
            for item in data.get("value", []):
                if "file" not in item:
                    continue
                mime = (item.get("file", {}) or {}).get("mimeType", "")
                if mime and not mime.startswith(_TEXTUAL):
                    continue
                dl = item.get("@microsoft.graph.downloadUrl")
                try:
                    if dl:
                        raw = http("GET", dl, headers={}, want="bytes")
                    else:
                        raw = http("GET", f"{_GRAPH}/drives/{item['parentReference']['driveId']}/items/{item['id']}/content",
                                   headers=headers, want="bytes")
                except Exception:  # noqa: BLE001
                    continue
                text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
                docs.append({"name": item.get("name"), "text": text, "url": item.get("webUrl")})
            url = data.get("@odata.nextLink")
        return docs
    return _client


class SharePointConnector(TokenConnector):
    service = "SharePoint"
    env_hint = "sign in with Microsoft (OAuth)"
    default_ext = ".txt"

    def _make_client(self):
        http = self._http or default_http(self.timeout)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        root = f"{_GRAPH}/sites/{self.target}/drive/root/children"
        return graph_drive_client(http, headers, root, self.max_items)
