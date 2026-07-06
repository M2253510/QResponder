"""Google Drive connector (Phase 12; hardened Phase 16) — Drive API v3, offline-tested.

Uses the user's OAuth access token (drive.readonly) against the Drive v3 REST API:
files.list scoped to the chosen folder (pageToken pagination), download binaries via
files.get?alt=media, and export Google-native docs via files.export to text/plain.
The HTTP fetcher is injectable so the real pagination/download logic runs offline
against real-API-shaped mocks. Runs only on explicit connect/test/sync.
"""

from __future__ import annotations

from urllib.parse import quote

from .base import TokenConnector, default_http

_API = "https://www.googleapis.com/drive/v3"
_TEXTUAL = ("text/", "application/rtf", "application/json", "application/xml")


class GoogleDriveConnector(TokenConnector):
    service = "Google Drive"
    env_hint = "sign in with Google (OAuth)"
    default_ext = ".txt"

    def _make_client(self):
        http = self._http or default_http(self.timeout)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

        def _client(folder_id: str):
            q = f"'{folder_id}' in parents and trashed=false" if folder_id else "trashed=false"
            docs, page = [], None
            while len(docs) < self.max_items:
                url = (f"{_API}/files?q={quote(q)}&pageSize=100&fields=nextPageToken,files(id,name,mimeType)"
                       + (f"&pageToken={page}" if page else ""))
                data = http("GET", url, headers=headers)
                for f in data.get("files", []):
                    mime = f.get("mimeType", "")
                    fid = f["id"]
                    try:
                        if mime == "application/vnd.google-apps.document":
                            raw = http("GET", f"{_API}/files/{fid}/export?mimeType=text/plain", headers=headers, want="bytes")
                        elif mime.startswith(_TEXTUAL):
                            raw = http("GET", f"{_API}/files/{fid}?alt=media", headers=headers, want="bytes")
                        else:
                            continue  # skip binaries the KB loaders don't read as text
                    except Exception:  # noqa: BLE001 - one unreadable file shouldn't sink the sync
                        continue
                    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
                    docs.append({"name": f.get("name"), "text": text,
                                 "url": f"https://drive.google.com/file/d/{fid}"})
                page = data.get("nextPageToken")
                if not page:
                    break
            return docs

        return _client
