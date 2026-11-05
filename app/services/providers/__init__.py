from functools import lru_cache

from app.config import Settings, settings
from app.services.providers.anthropic_provider import AnthropicProvider
from app.services.providers.base import LLMProvider, LLMProviderError, LLMResult
from app.services.providers.openai_provider import OpenAIProvider

__all__ = [
    "LLMProvider",
    "LLMProviderError",
    "LLMResult",
    "create_provider",
    "get_provider",
]


def create_provider(config: Settings) -> LLMProvider:
    """Crea el proveedor indicado por LLM_PROVIDER con su API key y modelo."""
    provider_name = config.llm_provider.lower()

    if provider_name == "anthropic":
        if not config.anthropic_api_key:
            raise LLMProviderError("Falta ANTHROPIC_API_KEY en el .env")
        return AnthropicProvider(
            api_key=config.anthropic_api_key,
            model=config.llm_model,
            max_tokens=config.max_tokens,
        )

    if provider_name == "openai":
        if not config.openai_api_key:
            raise LLMProviderError("Falta OPENAI_API_KEY en el .env")
        return OpenAIProvider(
            api_key=config.openai_api_key,
            model=config.llm_model,
            max_tokens=config.max_tokens,
        )

    raise LLMProviderError(
        f"Proveedor desconocido: '{config.llm_provider}'. Usá 'anthropic' u 'openai'."
    )


@lru_cache
def get_provider() -> LLMProvider:
    """Devuelve siempre la misma instancia del proveedor configurado."""
    return create_provider(settings)
