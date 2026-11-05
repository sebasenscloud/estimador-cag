import time
from collections.abc import Iterator
from textwrap import dedent

import structlog

from app.config import settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.services.cache import ExactMatchCache
from app.services.providers import LLMProvider, LLMResult, get_provider

logger = structlog.get_logger()

cache = ExactMatchCache(max_entries=settings.cache_max_entries)

SYSTEM_INSTRUCTIONS = (
    "Sos un estimador experto de proyectos de software. Tu trabajo es leer "
    "la transcripción de una reunión con un cliente y generar una estimación "
    "de esfuerzo.\n\n"
    "Basate en las estimaciones previas que aparecen abajo como referencia: "
    "usá el mismo formato (desglose de tareas con horas, total estimado, "
    "equipo recomendado y duración) y un criterio de horas coherente con ellas. "
    "Si la transcripción no tiene información suficiente para estimar alguna "
    "parte, indicalo y explicá qué supuestos tomaste."
)


def build_system_prompt() -> str:
    examples_text = ""
    for number, example in enumerate(ESTIMATION_EXAMPLES, start=1):
        examples_text += (
            f'<ejemplo numero="{number}">\n'
            f"<reunion>\n{example['meeting_summary'].strip()}\n</reunion>\n"
            f"<estimacion>\n{dedent(example['estimation']).strip()}\n</estimacion>\n"
            "</ejemplo>\n"
        )

    return f"{SYSTEM_INSTRUCTIONS}\n\n<ejemplos>\n{examples_text}</ejemplos>"


def _cache_key(provider: LLMProvider, system_prompt: str, transcript: str) -> str:
    return cache.make_key(provider.name, provider.model, system_prompt, transcript)


def _to_dict(result: LLMResult, cache_hit: bool) -> dict:
    return {
        "estimation": result.text,
        "provider": result.provider,
        "model": result.model,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cache_hit": cache_hit,
    }


def _log_completed(result: LLMResult, cache_hit: bool, streaming: bool, start: float):
    logger.info(
        "llm_request_completed",
        provider=result.provider,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_hit=cache_hit,
        streaming=streaming,
        duration_ms=round((time.perf_counter() - start) * 1000),
    )


def generate_estimation(transcript: str) -> dict:
    start = time.perf_counter()
    provider = get_provider()
    system_prompt = build_system_prompt()
    key = _cache_key(provider, system_prompt, transcript)

    cached = cache.get(key)
    if cached is not None:
        _log_completed(cached, cache_hit=True, streaming=False, start=start)
        return _to_dict(cached, cache_hit=True)

    try:
        result = provider.generate(system_prompt, transcript)
    except Exception as error:
        logger.error(
            "llm_request_failed",
            provider=provider.name,
            model=provider.model,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise

    cache.set(key, result)
    _log_completed(result, cache_hit=False, streaming=False, start=start)
    return _to_dict(result, cache_hit=False)


def stream_estimation(transcript: str, metrics: dict | None = None) -> Iterator[str]:
    start = time.perf_counter()
    provider = get_provider()
    system_prompt = build_system_prompt()
    key = _cache_key(provider, system_prompt, transcript)

    result = cache.get(key)
    cache_hit = result is not None

    if cache_hit:
        yield result.text
    else:
        usage = {}
        pieces = []
        try:
            for piece in provider.stream(system_prompt, transcript, usage):
                pieces.append(piece)
                yield piece
        except Exception as error:
            logger.error(
                "llm_request_failed",
                provider=provider.name,
                model=provider.model,
                streaming=True,
                error_type=type(error).__name__,
                error=str(error),
            )
            raise

        result = LLMResult(
            text="".join(pieces),
            provider=provider.name,
            model=usage.get("model", provider.model),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
        cache.set(key, result)

    _log_completed(result, cache_hit=cache_hit, streaming=True, start=start)

    if metrics is not None:
        metrics.update(_to_dict(result, cache_hit=cache_hit))
        metrics.pop("estimation")
