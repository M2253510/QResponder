"""In-context KB loader (§7 ladder, Phase 0 convenience: no retrieval infra).

Loads policy/evidence text files (Tier 2/3) from a directory into tag-aware
chunks and assembles a cited context block for the answering call. A `Tags:`
line in a file sets that file's tags. Past MAX_KB_CHARS it warns and recommends
`--mode retrieval` (Phase 1) rather than silently dropping content.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .base import KBChunk
from .tags import in_scope, normalize_tags

log = logging.getLogger("qresponder.kb")

_TEXT_EXTS = {".md", ".txt", ".markdown", ".rst"}
_TAGS_LINE = re.compile(r"^\s*tags?\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _extract_tags(text: str) -> list[str]:
    m = _TAGS_LINE.search(text)
    if not m:
        return []
    return normalize_tags(m.group(1))


def _split_paragraphs(text: str) -> list[str]:
    # Drop the Tags: line from the body, then split on blank lines.
    body = _TAGS_LINE.sub("", text)
    parts = re.split(r"\n\s*\n", body)
    return [p.strip() for p in parts if p.strip()]


class InContextKB:
    def __init__(self, chunks: list[KBChunk] | None = None):
        self.chunks: list[KBChunk] = chunks or []
        self._context_cache: dict[tuple, str] = {}

    @classmethod
    def load(cls, kb_dir: str | Path | None) -> "InContextKB":
        chunks: list[KBChunk] = []
        if kb_dir is None:
            return cls(chunks)
        d = Path(kb_dir)
        if not d.exists():
            return cls(chunks)
        for fp in sorted(d.rglob("*")):
            if not fp.is_file() or fp.suffix.lower() not in _TEXT_EXTS:
                continue
            text = fp.read_text(encoding="utf-8", errors="replace")
            tags = _extract_tags(text)
            for para in _split_paragraphs(text):
                chunks.append(
                    KBChunk(source=fp.name, text=para, tags=tags, tier=2)
                )
        log.info("Loaded %d KB chunk(s) from %s", len(chunks), kb_dir)
        return cls(chunks)

    def scoped(self, scope_tags=None) -> list[KBChunk]:
        return [c for c in self.chunks if in_scope(c.tags, scope_tags)]

    def assemble_context(self, scope_tags=None, max_chars: int = 150_000) -> str:
        """Build a cited context block from in-scope chunks, bounded by max_chars."""
        chunks = self.scoped(scope_tags)
        lines: list[str] = []
        total = 0
        truncated = False
        for c in chunks:
            block = f"[source: {c.source}] {c.text}"
            if total + len(block) > max_chars:
                truncated = True
                break
            lines.append(block)
            total += len(block)
        if truncated:
            log.warning(
                "KB context exceeded MAX_KB_CHARS (%d); truncated. "
                "Consider --mode retrieval for large knowledge bases.",
                max_chars,
            )
        return "\n\n".join(lines)

    def context_for(self, query: str, scope_tags=None, max_chars: int = 150_000) -> str:
        """Uniform context seam (B1). In-context mode ignores the query and
        returns the cached global assembled context for the given scope, so
        orchestration can treat in-context and retrieval KBs the same way."""
        key = (tuple(scope_tags) if scope_tags else (), max_chars)
        if key not in self._context_cache:
            self._context_cache[key] = self.assemble_context(scope_tags, max_chars)
        return self._context_cache[key]
