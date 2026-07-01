"""Google Drive connector (Phase 10 B; OAuth in Phase 12) — extras-gated, offline-tested.

Downloads text documents from one user-specified Drive folder using the user's own
OAuth access token (obtained via the browser sign-in flow; server-side only, never
sent to the browser). The Google client is lazy-imported from the `connectors`
extra; the client is injectable so tests run offline. Runs only on explicit
`connect gdrive`.
"""

from __future__ import annotations

from .base import ConnectorError, TokenConnector


class GoogleDriveConnector(TokenConnector):
    service = "Google Drive"
    env_hint = "sign in with Google (OAuth) or set a token in server config"
    default_ext = ".txt"

    def __init__(self, folder_id: str, token=None, tags=None, client=None,
                 timeout: int = 15, max_items: int = 200):
        super().__init__(folder_id, token=token, tags=tags, client=client,
                         timeout=timeout, max_items=max_items)

    def _make_client(self):  # pragma: no cover - real network/SDK path
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise ConnectorError(
                'Google Drive needs the optional extra: pip install "qresponder[connectors]".'
            ) from exc
        drive = build("drive", "v3", credentials=Credentials(token=self.token), cache_discovery=False)

        def _client(folder_id: str):
            docs, page = [], None
            q = f"'{folder_id}' in parents and trashed = false" if folder_id else "trashed = false"
            while len(docs) < self.max_items:
                resp = drive.files().list(q=q, pageSize=50, pageToken=page,
                                          fields="nextPageToken, files(id, name, mimeType)").execute()
                for f in resp.get("files", []):
                    mime = f.get("mimeType", "")
                    if mime == "application/vnd.google-apps.document":
                        data = drive.files().export(fileId=f["id"], mimeType="text/plain").execute()
                    elif mime.startswith("text/") or mime in ("application/rtf",):
                        data = drive.files().get_media(fileId=f["id"]).execute()
                    else:
                        continue
                    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
                    docs.append({"name": f.get("name"), "text": text,
                                 "url": f"https://drive.google.com/file/d/{f['id']}"})
                page = resp.get("nextPageToken")
                if not page:
                    break
            return docs

        return _client
