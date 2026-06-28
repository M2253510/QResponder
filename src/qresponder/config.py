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
    llm_provider: str = "anthropic"  # anthropic | openai_compat | mock

    # Anthropic path
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    # OpenAI-compatible path (Ollama / vLLM / LM Studio / OpenAI / OpenRouter)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "llama3.1"

    # --- Knowledge base -----------------------------------------------------
    kb_mode: str = "in_context"  # in_context | retrieval

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
    # Cross-encoder rerank score at/above which retrieval counts as "strong"
    # for the confidence rule (§11). Tunable per reranker.
    strong_rerank_score: float = 0.0

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
    "LLM_BASE_URL": "llm_base_url",
    "LLM_API_KEY": "llm_api_key",
    "LLM_MODEL": "llm_model",
    "KB_MODE": "kb_mode",
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
