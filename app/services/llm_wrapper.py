import time
from collections.abc import Iterator
from functools import lru_cache
from typing import TypeVar

import instructor
import litellm
import openai
import structlog
from instructor.core.exceptions import InstructorRetryException
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = structlog.get_logger()

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class LLMProviderError(Exception):
    """Falla al comunicarse con el proveedor (red, API key, límites, etc.)."""


class InvalidLLMOutputError(Exception):
    """El modelo no devolvió datos válidos ni siquiera después de los reintentos."""


@lru_cache
def get_client() -> instructor.Instructor:
    """Instructor sobre LiteLLM: una sola interfaz tipada para todos los proveedores."""
    return instructor.from_litellm(litellm.completion)


def provider_name(model: str) -> str:
    return litellm.get_llm_provider(model)[1]


def _api_key(model: str) -> str:
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
    system_prompt: str, user_message: str, response_model: type[ResponseModel]
) -> ResponseModel:
    """Pide al modelo una respuesta que cumpla `response_model`.

    Política FIX · RETRY: si la respuesta no pasa la validación de Pydantic,
    Instructor le reenvía el error al modelo hasta `LLM_MAX_RETRIES` veces.
    """
    start = time.perf_counter()
    model = settings.llm_model
    try:
        result, completion = get_client().chat.completions.create_with_completion(
            model=model,
            messages=_messages(system_prompt, user_message),
            response_model=response_model,
            max_retries=settings.llm_max_retries,
            max_tokens=settings.max_tokens,
            api_key=_api_key(model),
        )
    except InstructorRetryException as error:
        _log_failed(model, error)
        if isinstance(error.__cause__, openai.APIError):
            raise LLMProviderError(str(error.__cause__)) from error
        raise InvalidLLMOutputError(
            f"El modelo no devolvió una estimación válida tras "
            f"{error.n_attempts} intentos: {error}"
        ) from error
    except openai.APIError as error:
        _log_failed(model, error)
        raise LLMProviderError(str(error)) from error

    usage = getattr(completion, "usage", None)
    logger.info(
        "llm_request_completed",
        model=model,
        prompt_version=settings.prompt_version,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
        duration_ms=round((time.perf_counter() - start) * 1000),
    )
    return result


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
            api_key=_api_key(model),
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
