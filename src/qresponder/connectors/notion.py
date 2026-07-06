"""Notion connector (Phase 12; hardened Phase 16) — real Notion API, offline-tested.

Uses the Notion REST API (https://api.notion.com/v1) with a Bearer token and the
Notion-Version header: query the connected database, then read each page's block
children and extract text. Cursor pagination (start_cursor) to completion, bounded
by max_items. The HTTP fetcher is injectable so the real pagination/extraction logic
runs offline against real-API-shaped mocks. Runs only on explicit connect/test/sync.
"""

from __future__ import annotations

from .base import TokenConnector, default_http

_NOTION_VERSION = "2022-06-28"
_API = "https://api.notion.com/v1"


class NotionConnector(TokenConnector):
    service = "Notion"
    env_hint = "sign in with Notion (OAuth) or set notion_token in server config"
    default_ext = ".md"

    def _make_client(self):
        http = self._http or default_http(self.timeout)
        headers = {"Authorization": f"Bearer {self.token}", "Notion-Version": _NOTION_VERSION,
                   "Content-Type": "application/json", "Accept": "application/json"}

        def _text_of(page_id: str) -> str:
            parts, cursor = [], None
            while True:
                url = f"{_API}/blocks/{page_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
                data = http("GET", url, headers=headers)
                for b in data.get("results", []):
                    block = b.get(b.get("type"), {}) or {}
                    rt = block.get("rich_text", [])
                    line = "".join(t.get("plain_text", "") for t in rt)
                    if line:
                        parts.append(line)
                if not data.get("has_more"):
                    break
                cursor = data.get("next_cursor")
            return "\n".join(parts)

        def _client(database_id: str):
            docs, cursor = [], None
            while len(docs) < self.max_items:
                body = {"page_size": 50}
                if cursor:
                    body["start_cursor"] = cursor
                data = http("POST", f"{_API}/databases/{database_id}/query", headers=headers, body=body)
                for row in data.get("results", []):
                    title = ""
                    for prop in (row.get("properties") or {}).values():
                        if prop.get("type") == "title":
                            title = "".join(t.get("plain_text", "") for t in prop.get("title", []))
                            break
                    docs.append({"name": title or row.get("id"), "text": _text_of(row["id"]),
                                 "url": row.get("url")})
                if not data.get("has_more"):
                    break
                cursor = data.get("next_cursor")
            return docs

        return _client
