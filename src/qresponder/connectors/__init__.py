"""Source connectors (Phase 10 B) — point QRESPONDER at where docs already live.

BOUNDARY: connectors are the ONLY external-call path besides cloud answering, and
they run ONLY via an explicit `qresponder connect` command — never automatically,
never during answering. Each connector ingests into the workspace kb/ through the
existing bulk-ingest path (validation / sandboxing / provenance / tags reused).
"""

from .base import Connector, ConnectorError, SourceDoc, ingest_connector

__all__ = ["Connector", "ConnectorError", "SourceDoc", "ingest_connector"]
