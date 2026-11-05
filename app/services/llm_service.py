import time
from collections.abc import Iterator

import structlog

from app.config import settings
from app.prompts.loader import render_estimation_prompt
from app.schemas import EstimationRequest, EstimationResponse
from app.services.cache import ExactMatchCache
from app.services.providers import LLMProvider, LLMResult, get_provider

logger = structlog.get_logger()

cache = ExactMatchCache(max_entries=settings.cache_max_entries)


def _cache_key(provider: LLMProvider, system_prompt: str, user_message: str) -> str:
    return cache.make_key(provider.name, provider.model, system_prompt, user_message)


def _log_completed(
    result: LLMResult,
    request: EstimationRequest,
    cache_hit: bool,
    streaming: bool,
    start: float,
):
    logger.info(
        "llm_request_completed",
        provider=result.provider,
        model=result.model,
        prompt_version=settings.prompt_version,
        project_type=request.project_type.value,
        detail_level=request.detail_level.value,
        output_format=request.output_format.value,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_hit=cache_hit,
        streaming=streaming,
        duration_ms=round((time.perf_counter() - start) * 1000),
    )


def _log_failed(provider: LLMProvider, error: Exception, streaming: bool):
    logger.error(
        "llm_request_failed",
        provider=provider.name,
        model=provider.model,
        prompt_version=settings.prompt_version,
        streaming=streaming,
        error_type=type(error).__name__,
        error=str(error),
    )


def generate_estimation(request: EstimationRequest) -> EstimationResponse:
    start = time.perf_counter()
    provider = get_provider()
    system_prompt, user_message = render_estimation_prompt(
        request, version=settings.prompt_version
    )
    key = _cache_key(provider, system_prompt, user_message)

    result = cache.get(key)
    cache_hit = result is not None

    if not cache_hit:
        try:
            result = provider.generate(system_prompt, user_message)
        except Exception as error:
            _log_failed(provider, error, streaming=False)
            raise
        cache.set(key, result)

    _log_completed(result, request, cache_hit=cache_hit, streaming=False, start=start)
    return EstimationResponse(text=result.text, prompt_version=settings.prompt_version)


def stream_estimation(
    request: EstimationRequest, metrics: dict | None = None
) -> Iterator[str]:
    start = time.perf_counter()
    provider = get_provider()
    system_prompt, user_message = render_estimation_prompt(
        request, version=settings.prompt_version
    )
    key = _cache_key(provider, system_prompt, user_message)

    result = cache.get(key)
    cache_hit = result is not None

    if cache_hit:
        yield result.text
    else:
        usage = {}
        pieces = []
        try:
            for piece in provider.stream(system_prompt, user_message, usage):
                pieces.append(piece)
                yield piece
        except Exception as error:
            _log_failed(provider, error, streaming=True)
            raise

        result = LLMResult(
            text="".join(pieces),
            provider=provider.name,
            model=usage.get("model", provider.model),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
        cache.set(key, result)

    _log_completed(result, request, cache_hit=cache_hit, streaming=True, start=start)

    if metrics is not None:
        metrics.update(
            provider=result.provider,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_hit=cache_hit,
            prompt_version=settings.prompt_version,
        )
