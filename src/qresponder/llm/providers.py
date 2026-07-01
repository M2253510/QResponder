"""Multi-provider registry + routing (Phase 8 A).

Each cloud provider is configured by its OWN .env key (read server-side, never
sent to the browser). OpenAI / Gemini / DeepSeek go through the OpenAI-compatible
adapter with the right base URL; Anthropic uses the native adapter; `local` is
the generic OpenAI-compatible path (Ollama/vLLM/LM Studio); `mock` is test-only.
"""

from __future__ import annotations

from .base import ProviderError

# name -> spec. key_field is the Config attribute holding that provider's key.
PROVIDER_SPECS = {
    "openai": {
        "label": "OpenAI",
        "adapter": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "key_field": "openai_api_key",
        "models": {"type": "openai", "url": "https://api.openai.com/v1/models"},
        "default_model": "gpt-4o",
    },
    "gemini": {
        "label": "Google Gemini",
        "adapter": "openai_compat",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_field": "gemini_api_key",
        "models": {"type": "openai", "url": "https://generativelanguage.googleapis.com/v1beta/openai/models"},
        "default_model": "gemini-2.5-flash",
    },
    "deepseek": {
        "label": "DeepSeek",
        "adapter": "openai_compat",
        "base_url": "https://api.deepseek.com",
        "key_field": "deepseek_api_key",
        "models": {"type": "openai", "url": "https://api.deepseek.com/models"},
        "default_model": "deepseek-chat",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "adapter": "anthropic",
        "base_url": None,
        "key_field": "anthropic_api_key",
        "models": {"type": "anthropic", "url": "https://api.anthropic.com/v1/models"},
        "default_model": "claude-opus-4-8",
    },
    "local": {
        "label": "Local (Ollama / vLLM / LM Studio)",
        "adapter": "openai_compat",
        "base_url": None,  # uses config.llm_base_url
        "key_field": "llm_api_key",
        "models": {"type": "openai", "url": None},  # uses config.llm_base_url + /models
        "default_model": None,  # uses config.llm_model
    },
}

# Aliases for back-compat with the old llm_provider values.
_ALIASES = {"openai_compat": "local", "openai-compat": "local", "claude": "anthropic"}


def canonical(provider: str | None) -> str:
    p = (provider or "").strip().lower()
    return _ALIASES.get(p, p)


def provider_key(config, provider: str) -> str:
    spec = PROVIDER_SPECS.get(canonical(provider))
    if not spec:
        return ""
    return getattr(config, spec["key_field"], "") or ""


def is_configured(config, provider: str) -> bool:
    """A provider is configured if its key is present. `local` is configured when
    a base URL is set (local servers often need no real key); `mock` always."""
    p = canonical(provider)
    if p == "mock":
        return True
    if p == "local":
        return bool(getattr(config, "llm_base_url", ""))
    return bool(provider_key(config, p))


def model_for(config, provider: str, model: str | None) -> str:
    if model:
        return model
    p = canonical(provider)
    if p == "anthropic":
        return config.anthropic_model
    if p == "local":
        return config.llm_model
    return PROVIDER_SPECS.get(p, {}).get("default_model") or ""


def base_url_for(config, provider: str) -> str | None:
    p = canonical(provider)
    if p == "local":
        return config.llm_base_url
    return PROVIDER_SPECS.get(p, {}).get("base_url")


def make_provider_for(config, provider: str, model: str | None = None):
    """Build the right adapter for an explicitly-selected provider + model."""
    p = canonical(provider)
    if p == "mock":
        from .mock import MockProvider

        return MockProvider()
    spec = PROVIDER_SPECS.get(p)
    if spec is None:
        raise ProviderError(f"Unknown provider '{provider}'.")
    if not is_configured(config, p):
        raise ProviderError(
            f"{spec['label']} is not configured — set {_env_name(spec['key_field'])} in .env."
        )
    chosen_model = model_for(config, p, model)
    if not chosen_model:
        raise ProviderError(f"No model selected for {spec['label']}.")
    if spec["adapter"] == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key=provider_key(config, p), model=chosen_model)
    from .openai_compat_provider import OpenAICompatProvider

    key = provider_key(config, p) or getattr(config, "llm_api_key", "") or "not-needed"
    return OpenAICompatProvider(base_url=base_url_for(config, p), api_key=key, model=chosen_model)


def _env_name(key_field: str) -> str:
    return {
        "openai_api_key": "OPENAI_API_KEY",
        "gemini_api_key": "GEMINI_API_KEY",
        "deepseek_api_key": "DEEPSEEK_API_KEY",
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "llm_api_key": "LLM_API_KEY",
    }.get(key_field, key_field.upper())
