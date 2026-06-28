"""Shared KB data structures + a lexical similarity helper.

The `KBChunk` is the common unit passed to the answering layer (with its source
and tier so citations and confidence can be derived). The lexical similarity
function is the Phase 0 stand-in for semantic matching — Phase 1 swaps in
embeddings + reranker behind the same orchestration seam.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from pydantic import BaseModel, Field


class KBChunk(BaseModel):
    source: str
    text: str
    tags: list[str] = Field(default_factory=list)
    tier: int = 2  # 1=Library, 2=Policies, 3=Evidence


_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def lexical_similarity(a: str, b: str) -> float:
    """Blend token Jaccard with a sequence ratio. Range [0, 1].

    Token Jaccard captures shared vocabulary (control names, acronyms); the
    sequence ratio rewards similar phrasing/order. The average is a stable,
    dependency-free proxy for semantic match in Phase 0.
    """
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return (jaccard + ratio) / 2.0


def snippet_supported(snippet: str, context: str, min_overlap: float = 0.6) -> bool:
    """True if the snippet is plausibly drawn from context (F2).

    A normalized substring, or >= min_overlap of its content tokens present in
    the context. A cheap, no-model guard against a model citing a
    plausible-but-absent snippet (the fabricated-citation failure class).
    """
    norm = lambda s: re.sub(r"\s+", " ", s.lower()).strip()  # noqa: E731
    ns, nc = norm(snippet), norm(context)
    if not ns:
        return False
    if ns in nc:
        return True
    toks = [w for w in _tokens(snippet) if len(w) > 2]
    if not toks:
        return False
    return sum(1 for w in toks if w in nc) / len(toks) >= min_overlap
