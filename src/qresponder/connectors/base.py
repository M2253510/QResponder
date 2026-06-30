"""Connector interface + shared ingestion (Phase 10 B)."""

from __future__ import annotations

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
    `connect` command — never during answering."""

    @abstractmethod
    def fetch(self) -> list[SourceDoc]: ...


def ingest_connector(connector: Connector, kb_dir, tags=None, allowed=None) -> dict:
    """Run a connector and ingest its docs into kb_dir via the bulk-ingest path
    (reusing validation/sandboxing/provenance/tagging)."""
    allowed = allowed or KB_INGEST_EXTS
    docs = connector.fetch()
    items = [(d.source_name, d.content) for d in docs]
    # Per-doc tags would need per-file sidecar writes; connectors apply one tag set.
    return ingest_files(items, kb_dir, allowed, tags=tags)
