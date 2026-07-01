"""Notion connector (Phase 12) — extras-gated, injectable client, offline-tested.

Fetches pages from one user-specified Notion database using the user's own
integration token (server-side config, never the browser), and ingests them via
the existing bulk path. The real client is lazy-imported so the slim image and
import-guard hold. Runs only on explicit `connect notion`.
"""

from __future__ import annotations

from .base import ConnectorError, TokenConnector


class NotionConnector(TokenConnector):
    service = "Notion"
    env_hint = "set notion_token in .env"
    default_ext = ".md"

    def _make_client(self):  # pragma: no cover - real network/SDK path
        try:
            from notion_client import Client  # type: ignore
        except ImportError as exc:
            raise ConnectorError(
                'Notion needs the optional extra: pip install "qresponder[connectors]".'
            ) from exc
        notion = Client(auth=self.token)

        def _text_of(page_id: str) -> str:
            blocks = notion.blocks.children.list(block_id=page_id).get("results", [])
            parts = []
            for b in blocks:
                rt = (b.get(b.get("type"), {}) or {}).get("rich_text", [])
                parts.append("".join(t.get("plain_text", "") for t in rt))
            return "\n".join(p for p in parts if p)

        def _client(database_id: str):
            docs, cursor = [], None
            while len(docs) < self.max_items:
                q = notion.databases.query(database_id=database_id, start_cursor=cursor, page_size=50)
                for row in q.get("results", []):
                    title = ""
                    for prop in (row.get("properties") or {}).values():
                        if prop.get("type") == "title":
                            title = "".join(t.get("plain_text", "") for t in prop.get("title", []))
                            break
                    docs.append({"name": title or row.get("id"), "text": _text_of(row["id"]),
                                 "url": row.get("url")})
                if not q.get("has_more"):
                    break
                cursor = q.get("next_cursor")
            return docs

        return _client
