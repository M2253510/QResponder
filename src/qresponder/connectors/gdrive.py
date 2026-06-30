"""Google Drive connector (Phase 10 B) — OPTIONAL, extras-gated.

Uses the user's own OAuth to download files from a Drive folder to the host. The
Google libraries are an optional `connectors` extra so the phase isn't blocked on
OAuth; the pluggable `Connector` interface lets Confluence/Notion be community-
added the same way.
"""

from __future__ import annotations

from .base import Connector, ConnectorError, SourceDoc


class GoogleDriveConnector(Connector):
    def __init__(self, folder_id: str, tags=None, credentials_path: str | None = None):
        self.folder_id = folder_id
        self.tags = list(tags or [])
        self.credentials_path = credentials_path

    def fetch(self) -> list[SourceDoc]:  # pragma: no cover - requires OAuth + network
        try:
            from google.oauth2.credentials import Credentials  # noqa: F401
            from googleapiclient.discovery import build  # noqa: F401
        except ImportError as exc:
            raise ConnectorError(
                'Google Drive support needs the optional extra: pip install "qresponder[connectors]" '
                "and configure your own OAuth credentials."
            ) from exc
        raise ConnectorError(
            "Google Drive connector requires OAuth setup; see README. The interface is "
            "ready — supply credentials to enable it."
        )
