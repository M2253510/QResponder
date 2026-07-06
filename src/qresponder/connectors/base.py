"""Connector interface + shared ingestion (Phase 10 B; extended Phase 12)."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..core.bulk_ingest import KB_INGEST_EXTS, ingest_files


class ConnectorError(RuntimeError):
    pass


@dataclass
class SourceDoc:
    source_name: str          # filename to store under (sanitized on ingest)
    content: bytes            # raw bytes for the existing KB loaders
    origin: str               # url or filesystem path it came from
    tags: list[str] = field(default_factory=list)
    fetched_at: str | None = None


class Connector(ABC):
    """Fetches documents from a user-specified source. Called only by an explicit
    `connect`/`test`/`sync` — never during answering."""

    @abstractmethod
    def fetch(self) -> list[SourceDoc]: ...

    def test_connection(self) -> dict:
        """Prowler-style reachability check: {ok, detail}. Default tries a fetch and
        never leaks a secret in the detail (only the error type/message). Subclasses
        override with a cheaper probe."""
        try:
            docs = self.fetch()
            return {"ok": True, "detail": f"Reached the source — {len(docs)} document(s) visible."}
        except ConnectorError as exc:
            return {"ok": False, "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def default_http(timeout: int = 15):  # pragma: no cover - real network
    """Stdlib HTTP fetcher matching the injectable contract:
    http(method, url, headers=None, body=None, want="json") -> dict | bytes."""
    import json as _json
    import urllib.request

    def _http(method, url, headers=None, body=None, want="json"):
        data = _json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - explicit connector fetch
            raw = resp.read()
        return _json.loads(raw.decode("utf-8")) if want == "json" else raw

    return _http


def _doc_name(raw: str, default_ext: str) -> str:
    """A safe, KB-ingestable filename from a document title or filename. A real
    trailing extension is preserved (so 'a.txt' stays '.txt'); a title with no
    known extension gets the connector's default_ext."""
    raw = (raw or "doc").strip()
    ext = ""
    low = raw.lower()
    for e in KB_INGEST_EXTS:
        if low.endswith(e):
            ext = e
            raw = raw[: -len(e)]
            break
    slug = (re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-") or "doc")[:80]
    return slug + (ext or default_ext)


class TokenConnector(Connector):
    """Shared base for credentialed SaaS connectors (Confluence/Notion/SharePoint/
    OneDrive). The credential comes from server-side config (never the browser); the
    SaaS client is INJECTABLE — real clients lazy-import an extras-gated SDK, tests
    pass a fake so everything runs offline. Bounded by max_items; a user-supplied
    base URL is SSRF-guarded. Runs only on explicit `connect`, never during answering.

    Injected/real client contract: ``client(target) -> list[dict]`` where each dict
    has ``text``/``content`` and optionally ``name``/``title`` and ``url``/``origin``.
    """

    service = "service"
    env_hint = "set its token in .env / server config"
    default_ext = ".txt"

    def __init__(self, target: str, token: str | None = None, base_url: str | None = None,
                 tags=None, client=None, http=None, timeout: int = 15, max_items: int = 200):
        self.target = target
        self.token = token
        self.base_url = base_url
        self.tags = list(tags or [])
        self._client = client
        # Injectable HTTP fetcher: http(method, url, headers=..., stream=False) -> dict|bytes.
        # When set, the real per-provider client runs against it, so pagination/refresh
        # logic is exercised OFFLINE against real-API-shaped mocks (no SDK, no network).
        self._http = http
        self.timeout = timeout
        self.max_items = max(1, max_items)

    def test_connection(self) -> dict:
        """Clamp the fetch to a few items so a reachability check is cheap."""
        orig = self.max_items
        self.max_items = min(3, orig)
        try:
            return super().test_connection()
        finally:
            self.max_items = orig

    def _make_client(self):  # pragma: no cover - real network/SDK path
        """Build the real SaaS client, lazy-importing its extras-gated SDK. Subclasses
        override; on a missing SDK they raise a ConnectorError pointing at the extra."""
        raise ConnectorError(f"{self.service}: no client configured.")

    def _guard(self) -> None:
        if self.base_url:
            from .website import ssrf_ok

            if not ssrf_ok(self.base_url):
                raise ConnectorError(f"Blocked by SSRF guard (localhost/private/metadata): {self.base_url}")

    def fetch(self) -> list[SourceDoc]:
        self._guard()
        client = self._client
        if client is None:
            if not self.token:
                raise ConnectorError(f"{self.service}: missing credential — {self.env_hint}.")
            client = self._make_client()
        raw = client(self.target) if callable(client) else client.fetch(self.target)
        out: list[SourceDoc] = []
        for d in list(raw)[: self.max_items]:
            text = (d.get("text") or d.get("content") or "").strip()
            if not text:
                continue
            out.append(SourceDoc(
                source_name=_doc_name(d.get("name") or d.get("title") or "doc", self.default_ext),
                content=text.encode("utf-8"),
                origin=d.get("origin") or d.get("url") or f"{self.service}:{self.target}",
                tags=self.tags))
        return out


def ingest_connector(connector: Connector, kb_dir, tags=None, allowed=None) -> dict:
    """Run a connector and ingest its docs into kb_dir via the bulk-ingest path
    (reusing validation/sandboxing/provenance/tagging)."""
    allowed = allowed or KB_INGEST_EXTS
    docs = connector.fetch()
    items = [(d.source_name, d.content) for d in docs]
    # Per-doc tags would need per-file sidecar writes; connectors apply one tag set.
    return ingest_files(items, kb_dir, allowed, tags=tags)
