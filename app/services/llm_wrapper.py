import time
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Generic, TypeVar

import instructor
import litellm
import openai
import structlog
from instructor.core.exceptions import InstructorRetryException
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.schemas.llm import LLMCallMetrics

logger = structlog.get_logger()

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)

# Precios en USD por millón de tokens: (entrada, salida). Se buscan por el
# nombre del modelo sin el prefijo del proveedor ("anthropic/claude-haiku-4-5"
# → "claude-haiku-4-5"). Un modelo que no esté acá devuelve cost_usd=None.
MODEL_COSTS: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}


@dataclass(frozen=True)
class LLMCall(Generic[ResponseModel]):
    """Lo que devuelve cada llamada: el resultado validado y sus métricas."""

    result: ResponseModel
    metrics: LLMCallMetrics


def estimate_cost(
    model: str, tokens_in: int | None, tokens_out: int | None
) -> float | None:
    prices = MODEL_COSTS.get(model.split("/", 1)[-1])
    if prices is None or tokens_in is None or tokens_out is None:
        return None
    price_in, price_out = prices
    return round((tokens_in * price_in + tokens_out * price_out) / 1_000_000, 6)


class LLMProviderError(Exception):
    """Falla al comunicarse con el proveedor (red, API key, límites, etc.)."""


class InvalidLLMOutputError(Exception):
    """El modelo no devolvió datos válidos ni siquiera después de los reintentos."""


def _completion(*args, **kwargs):
    """`litellm.completion` adaptado para el cliente síncrono de Instructor.

    En streaming, LiteLLM devuelve un CustomStreamWrapper que implementa a la
    vez `__iter__` y `__aiter__`. Instructor lo detecta como iterador asíncrono
    y devuelve un generador async, que no se puede recorrer con un `for`
    normal. Envolverlo en un generador síncrono evita esa ambigüedad.
    """
    response = litellm.completion(*args, **kwargs)
    if kwargs.get("stream"):
        return (chunk for chunk in response)
    return response


@lru_cache
def get_client() -> instructor.Instructor:
    """Instructor sobre LiteLLM: una sola interfaz tipada para todos los proveedores."""
    return instructor.from_litellm(_completion)


def provider_name(model: str) -> str:
    return litellm.get_llm_provider(model)[1]


def api_key_for(model: str) -> str:
    keys = {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
    }
    provider = provider_name(model)
    api_key = keys.get(provider)
    if not api_key:
        raise LLMProviderError(
            f"Falta la API key de '{provider}' en el .env para el modelo '{model}'."
        )
    return api_key


def _messages(system_prompt: str, user_message: str) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]


def generate_structured(
    system_prompt: str,
    user_message: str,
    response_model: type[ResponseModel],
    purpose: str = "estimation",
) -> LLMCall[ResponseModel]:
    """Atajo para un solo turno: system + user como texto."""
    return generate_structured_from_messages(
        _messages(system_prompt, user_message), response_model, purpose=purpose
    )


def generate_structured_from_messages(
    messages: list[dict],
    response_model: type[ResponseModel],
    purpose: str = "estimation",
) -> LLMCall[ResponseModel]:
    """Pide al modelo una respuesta que cumpla `response_model`.

    Recibe el array `messages` completo, en el formato de LiteLLM/OpenAI. El
    `content` de un mensaje puede ser un string o una lista de bloques
    ({"type": "text"}, {"type": "file"}, ...): así sirve para adjuntos de
    cualquier camino y para el historial de una sesión.

    Política FIX · RETRY: si la respuesta no pasa la validación de Pydantic,
    Instructor le reenvía el error al modelo hasta `LLM_MAX_RETRIES` veces.

    Devuelve un `LLMCall` con el resultado y sus métricas (latencia, tokens,
    costo, modelo y proveedor); `purpose` identifica para qué se hizo la llamada.
    """
    start = time.perf_counter()
    model = settings.llm_model
    try:
        result, completion = get_client().chat.completions.create_with_completion(
            model=model,
            messages=messages,
            response_model=response_model,
            max_retries=settings.llm_max_retries,
            max_tokens=settings.max_tokens,
            api_key=api_key_for(model),
        )
    except InstructorRetryException as error:
        _log_failed(model, error)
        if isinstance(error.__cause__, openai.APIError):
            raise LLMProviderError(str(error.__cause__)) from error
        raise InvalidLLMOutputError(
            f"El modelo no devolvió una respuesta válida tras "
            f"{error.n_attempts} intentos: {error}"
        ) from error
    except openai.APIError as error:
        _log_failed(model, error)
        raise LLMProviderError(str(error)) from error

    usage = getattr(completion, "usage", None)
    tokens_in = getattr(usage, "prompt_tokens", None)
    tokens_out = getattr(usage, "completion_tokens", None)
    metrics = LLMCallMetrics(
        purpose=purpose,
        model=model,
        provider=provider_name(model),
        latency_ms=round((time.perf_counter() - start) * 1000),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=estimate_cost(model, tokens_in, tokens_out),
    )
    logger.info(
        "llm_request_completed",
        prompt_version=settings.prompt_version,
        **metrics.model_dump(),
    )
    return LLMCall(result=result, metrics=metrics)


def stream_structured(
    system_prompt: str, user_message: str, response_model: type[ResponseModel]
) -> Iterator[BaseModel]:
    """Streaming parcial: entrega el objeto a medida que el modelo lo completa.

    Cada elemento es una versión parcial de `response_model` (campos aún sin
    generar en None). Mientras el JSON está incompleto no se valida; cuando se
    completa, Instructor lo valida contra `response_model`.
    """
    start = time.perf_counter()
    model = settings.llm_model
    try:
        yield from get_client().chat.completions.create_partial(
            model=model,
            messages=_messages(system_prompt, user_message),
            response_model=response_model,
            max_retries=settings.llm_max_retries,
            max_tokens=settings.max_tokens,
            api_key=api_key_for(model),
        )
    except InstructorRetryException as error:
        _log_failed(model, error)
        if isinstance(error.__cause__, openai.APIError):
            raise LLMProviderError(str(error.__cause__)) from error
        raise InvalidLLMOutputError(str(error)) from error
    except ValidationError as error:
        _log_failed(model, error)
        raise InvalidLLMOutputError(str(error)) from error
    except openai.APIError as error:
        _log_failed(model, error)
        raise LLMProviderError(str(error)) from error

    logger.info(
        "llm_stream_completed",
        model=model,
        prompt_version=settings.prompt_version,
        duration_ms=round((time.perf_counter() - start) * 1000),
    )


def _log_failed(model: str, error: Exception) -> None:
    logger.error(
        "llm_request_failed",
        model=model,
        prompt_version=settings.prompt_version,
        error_type=type(error).__name__,
        error=str(error),
    )
