"""Per-workspace source connections (Phase 14) — a Prowler-style "Add Provider"
model over the existing connectors.

A connection is the non-secret record of a configured source
({id, type, label, config, status, last_synced, created_at}). Secrets/tokens are
NEVER part of that record — they live in a separate server-side secret store keyed
by connection id, so a Connection can be listed/returned to the browser with no risk
of leaking a credential. Connectors fetch only on an explicit test/sync.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

CONNECTION_TYPES = {"folder", "website", "gdrive", "confluence", "notion", "sharepoint", "onedrive"}
# Field names that must never be written into the non-secret connection record.
_SECRET_KEYS = {"token", "secret", "access_token", "refresh_token", "client_secret",
                "password", "key", "api_key", "apikey", "cloud_id"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def strip_secrets(config: dict | None) -> dict:
    """Drop any secret-ish keys — the connection record holds NON-secret config only."""
    return {k: v for k, v in (config or {}).items() if k.lower() not in _SECRET_KEYS}


class Connection(BaseModel):
    id: str
    type: str
    label: str
    config: dict = Field(default_factory=dict)   # NON-secret only
    status: str = "configured"                    # configured | connected | error | needs_auth
    last_synced: str | None = None
    created_at: str | None = None


class ConnectionStore:
    """Persists connections (non-secret) + a separate secret store, per workspace."""

    def __init__(self, ws_path):
        self.path = Path(ws_path)
        self.file = self.path / "connections.json"
        self.secret_dir = self.path / ".secrets"
        self.secret_file = self.secret_dir / "connections.json"

    # -- non-secret records --
    def _load(self) -> list[dict]:
        if self.file.exists():
            try:
                return json.loads(self.file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                return []
        return []

    def _save(self, rows: list[dict]) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    # -- secret store (never returned to the browser) --
    def _load_secrets(self) -> dict:
        if self.secret_file.exists():
            try:
                return json.loads(self.secret_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                return {}
        return {}

    def _save_secrets(self, d: dict) -> None:
        self.secret_dir.mkdir(parents=True, exist_ok=True)
        self.secret_file.write_text(json.dumps(d), encoding="utf-8")
        try:  # best-effort restrictive perms (POSIX; a no-op cost on Windows)
            os.chmod(self.secret_file, 0o600)
        except OSError:
            pass

    def list(self) -> list[Connection]:
        return [Connection(**r) for r in self._load()]

    def get(self, cid: str) -> Connection | None:
        for r in self._load():
            if r["id"] == cid:
                return Connection(**r)
        return None

    def create(self, type: str, label: str | None, config: dict | None = None,
               secret: dict | None = None, status: str = "configured") -> Connection:
        cid = uuid.uuid4().hex[:12]
        conn = Connection(id=cid, type=type, label=(label or type).strip() or type,
                          config=strip_secrets(config), status=status, created_at=_now())
        rows = self._load()
        rows.append(json.loads(conn.model_dump_json()))
        self._save(rows)
        if secret:
            self.set_secret(cid, secret)
        return conn

    def update(self, cid: str, label=None, config=None, status=None, last_synced=None) -> Connection | None:
        rows = self._load()
        out = None
        for r in rows:
            if r["id"] == cid:
                if label is not None:
                    r["label"] = label
                if config is not None:
                    r["config"] = strip_secrets({**r.get("config", {}), **config})
                if status is not None:
                    r["status"] = status
                if last_synced is not None:
                    r["last_synced"] = last_synced
                out = Connection(**r)
        self._save(rows)
        return out

    def delete(self, cid: str) -> None:
        self._save([r for r in self._load() if r["id"] != cid])
        secrets = self._load_secrets()
        if cid in secrets:
            del secrets[cid]
            self._save_secrets(secrets)

    def set_secret(self, cid: str, secret: dict) -> None:
        d = self._load_secrets()
        d[cid] = secret
        self._save_secrets(d)

    def get_secret(self, cid: str) -> dict | None:
        return self._load_secrets().get(cid)


def build_connector(type: str, config: dict, secret: dict | None, tags=None, client=None,
                    http=None, probe: bool = False):
    """Map a connection (type + non-secret config + server-side secret) to a live
    connector. `client` (docs) and `http` (real API) are injectable so tests never hit
    a SaaS API; `probe` caps the fetch for a lightweight Test connection. Lazy imports
    keep the slim image."""
    from ..connectors.base import ConnectorError

    cfg = config or {}
    secret = secret or {}
    token = secret.get("access_token") or secret.get("token")
    cap = 3 if probe else int(cfg.get("max_items", 200))

    if type == "folder":
        from ..connectors.folder import FolderConnector

        return FolderConnector(cfg.get("path", ""), tags=tags)
    if type == "website":
        from ..connectors.website import WebsiteConnector

        return WebsiteConnector(cfg.get("url", ""), depth=int(cfg.get("depth", 1)),
                                max_pages=(1 if probe else int(cfg.get("max_pages", 20))),
                                allow_private=bool(cfg.get("allow_private", False)), tags=tags)
    if type == "gdrive":
        from ..connectors.gdrive import GoogleDriveConnector

        return GoogleDriveConnector(cfg.get("folder_id", ""), token=token, tags=tags, client=client, http=http, max_items=cap)
    if type == "confluence":
        from ..connectors.confluence import ConfluenceConnector

        return ConfluenceConnector(cfg.get("space", ""), token=token, base_url=cfg.get("base_url"),
                                   email=cfg.get("email"), cloud_id=secret.get("cloud_id"),
                                   tags=tags, client=client, max_items=cap)
    if type == "notion":
        from ..connectors.notion import NotionConnector

        return NotionConnector(cfg.get("database", ""), token=token, tags=tags, client=client, http=http, max_items=cap)
    if type == "sharepoint":
        from ..connectors.sharepoint import SharePointConnector

        return SharePointConnector(cfg.get("site", ""), token=token, tags=tags, client=client, http=http, max_items=cap)
    if type == "onedrive":
        from ..connectors.onedrive import OneDriveConnector

        return OneDriveConnector(cfg.get("folder", ""), token=token, tags=tags, client=client, http=http, max_items=cap)
    raise ConnectorError(f"unknown connection type: {type}")
