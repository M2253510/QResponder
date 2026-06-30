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
from .tags import in_scope, load_tag_sidecar, normalize_tags

log = logging.getLogger("qresponder.kb")

_TEXT_EXTS = {".md", ".txt", ".markdown", ".rst", ".csv"}
# Other KB document formats; text extracted via the ingest loaders / converters.
_DOC_EXTS = {".pdf", ".docx", ".xlsx", ".xlsm", ".html", ".htm"}
_KB_EXTS = _TEXT_EXTS | _DOC_EXTS
_TAGS_LINE = re.compile(r"^\s*tags?\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_TAG_STRIP = re.compile(r"<[^>]+>")


def _read_doc_text(fp: Path) -> str:
    """Plain text for a KB document. Text/CSV read directly; HTML stripped of
    tags; PDF/DOCX/XLSX extracted via the ingest loaders (reusing engine code)."""
    ext = fp.suffix.lower()
    if ext in _TEXT_EXTS:
        return fp.read_text(encoding="utf-8", errors="replace")
    if ext in {".html", ".htm"}:
        raw = fp.read_text(encoding="utf-8", errors="replace")
        return _TAG_STRIP.sub(" ", raw)
    try:
        from ..ingest.base import load_document

        doc = load_document(fp)
        return "\n\n".join(e.text for e in doc.elements)
    except Exception:  # noqa: BLE001 - unreadable doc just contributes nothing
        return ""


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
        sidecar = load_tag_sidecar(d)  # UI-assigned tags take precedence
        for fp in sorted(d.rglob("*")):
            if not fp.is_file() or fp.suffix.lower() not in _KB_EXTS:
                continue
            text = _read_doc_text(fp)
            tags = sidecar.get(fp.name) or _extract_tags(text)
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
