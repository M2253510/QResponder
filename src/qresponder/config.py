"""Configuration loading for QRESPONDER.

Resolution order (later wins): built-in defaults -> config.yaml (if present) ->
environment variables (incl. a .env file loaded manually, no extra dependency).

Guardrail (§4.7): never log secrets. The Config object intentionally has no
__repr__ that dumps keys; callers should print specific non-secret fields only.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


def _load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Avoids a python-dotenv dependency.

    Does not override variables already present in the real environment.
    """
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class Config(BaseModel):
    # --- Provider selection -------------------------------------------------
    # anthropic | openai | gemini | deepseek | local (openai_compat) | mock
    llm_provider: str = "anthropic"

    # Anthropic path
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    # Cloud provider keys (Phase 8 A) — server-side only, never sent to the browser.
    openai_api_key: str = ""
    gemini_api_key: str = ""
    deepseek_api_key: str = ""

    # Source-connector credentials (Phase 12) — server-side only, never in the browser.
    # Used ONLY by an explicit `connect`; never touched during answering.
    confluence_token: str = ""
    confluence_base_url: str = ""
    confluence_email: str = ""
    notion_token: str = ""
    microsoft_token: str = ""  # MS Graph token for SharePoint + OneDrive

    # Local / generic OpenAI-compatible path (Ollama / vLLM / LM Studio / OpenRouter)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "llama3.1"

    # --- Knowledge base -----------------------------------------------------
    kb_mode: str = "in_context"  # in_context | retrieval
    evidence_dir: str | None = None  # evidence vault for attachment resolution (C2)

    # --- Web UI -------------------------------------------------------------
    workspaces_dir: str = "./workspaces"  # where per-workspace asset bundles live
    # Analytics (Phase 10 D): minutes a human would spend per question, used only
    # for the labeled time-saved ESTIMATE. Local read; no telemetry.
    stats_minutes_per_question: float = 10.0

    # --- Embeddings / reranker (Phase 1, local-first) -----------------------
    embedding_model: str = "all-MiniLM-L6-v2"
    reranker_model: str = "BAAI/bge-reranker-base"
    top_n_retrieve: int = 20
    top_k_context: int = 5
    rrf_k: int = 60

    # --- Limits / behavior --------------------------------------------------
    max_kb_chars: int = 150_000
    verify_faithfulness: bool = True
    batch_size: int = 12
    # Duplicate-question grouping (Part E): answer the canonical once, apply to
    # near-duplicates within a run.
    dedup_questions: bool = True
    dedup_threshold: float = 0.9
    # SME routing (Part E): map of tag -> owner for flagged items.
    owners: dict = {}
    # Acronym glossary for query normalization (Part G3): acronym -> expansion.
    glossary: dict = {}
    # Cross-source conflict detection (D1).
    detect_conflicts: bool = True
    conflict_use_judge: bool = True       # LLM-judge for nuanced (non-heuristic) pairs
    # Only compare questions at least this similar. Short paraphrased questions
    # ("Do you encrypt at rest?" vs "Is data at rest encrypted?") score ~0.43
    # lexically; unrelated questions score ~0.17 — 0.4 separates them.
    conflict_similarity_floor: float = 0.4
    # Cross-encoder rerank score at/above which retrieval counts as "strong"
    # for the confidence rule (§11). Reranker-dependent — tune via `eval`.
    strong_rerank_score: float = 0.0
    # In-context mode has no reranker; instead a [0,1] grounding score (answer↔
    # cited snippet similarity) gates HIGH. Default tuned so verbatim-grounded
    # answers qualify and loosely-grounded ones stay MEDIUM.
    strong_grounding_score: float = 0.85

    extra: dict = Field(default_factory=dict)

    # Convenience -----------------------------------------------------------
    def safe_summary(self) -> dict:
        """Non-secret view of the config, for logging / doctor output."""
        return {
            "llm_provider": self.llm_provider,
            "anthropic_model": self.anthropic_model,
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
            "kb_mode": self.kb_mode,
            "embedding_model": self.embedding_model,
            "reranker_model": self.reranker_model,
            "top_n_retrieve": self.top_n_retrieve,
            "top_k_context": self.top_k_context,
            "rrf_k": self.rrf_k,
            "verify_faithfulness": self.verify_faithfulness,
            "batch_size": self.batch_size,
        }


def _coerce(model_fields: dict, key: str, value: str):
    """Coerce an env string to the field's declared type (int/bool/str)."""
    field = model_fields.get(key)
    if field is None:
        return value
    annotation = field.annotation
    if annotation is bool:
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if annotation is int:
        try:
            return int(value)
        except ValueError:
            return value
    return value


# Mapping from environment variable names to Config field names.
_ENV_MAP = {
    "LLM_PROVIDER": "llm_provider",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "ANTHROPIC_MODEL": "anthropic_model",
    "OPENAI_API_KEY": "openai_api_key",
    "GEMINI_API_KEY": "gemini_api_key",
    "DEEPSEEK_API_KEY": "deepseek_api_key",
    "CONFLUENCE_TOKEN": "confluence_token",
    "CONFLUENCE_BASE_URL": "confluence_base_url",
    "CONFLUENCE_EMAIL": "confluence_email",
    "NOTION_TOKEN": "notion_token",
    "MICROSOFT_TOKEN": "microsoft_token",
    "LLM_BASE_URL": "llm_base_url",
    "LLM_API_KEY": "llm_api_key",
    "LLM_MODEL": "llm_model",
    "KB_MODE": "kb_mode",
    "EVIDENCE_DIR": "evidence_dir",
    "WORKSPACES_DIR": "workspaces_dir",
    "EMBEDDING_MODEL": "embedding_model",
    "RERANKER_MODEL": "reranker_model",
    "TOP_N_RETRIEVE": "top_n_retrieve",
    "TOP_K_CONTEXT": "top_k_context",
    "RRF_K": "rrf_k",
    "MAX_KB_CHARS": "max_kb_chars",
    "VERIFY_FAITHFULNESS": "verify_faithfulness",
    "BATCH_SIZE": "batch_size",
}


def load_config(
    config_path: str | Path | None = "config.yaml",
    env_path: str | Path | None = ".env",
) -> Config:
    """Load configuration from defaults, optional YAML, then environment."""
    if env_path is not None:
        _load_dotenv(env_path)

    data: dict = {}
    if config_path is not None:
        p = Path(config_path)
        if p.exists():
            loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                data.update(loaded)

    fields = Config.model_fields
    for env_key, field_name in _ENV_MAP.items():
        if env_key in os.environ:
            data[field_name] = _coerce(fields, field_name, os.environ[env_key])

    return Config(**data)
