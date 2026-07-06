"""OneDrive connector (Phase 12; hardened Phase 16) — Microsoft Graph, offline-tested.

Uses the user's Microsoft OAuth token (Files.Read.All) against Graph
(https://graph.microsoft.com/v1.0): list items under the chosen folder in the user's
drive, download textual files via /content. @odata.nextLink pagination to completion,
bounded by max_items. Shares the Graph drive walk with the SharePoint connector; the
HTTP fetcher is injectable so it runs offline. Runs only on explicit connect/test/sync.
"""

from __future__ import annotations

from .base import TokenConnector, default_http
from .sharepoint import _GRAPH, graph_drive_client


class OneDriveConnector(TokenConnector):
    service = "OneDrive"
    env_hint = "sign in with Microsoft (OAuth)"
    default_ext = ".txt"

    def _make_client(self):
        http = self._http or default_http(self.timeout)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        folder = (self.target or "").strip("/")
        root = (f"{_GRAPH}/me/drive/root:/{folder}:/children" if folder
                else f"{_GRAPH}/me/drive/root/children")
        return graph_drive_client(http, headers, root, self.max_items)
