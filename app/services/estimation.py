"""Caso de uso: estimar un proyecto.

Orquesta el pipeline y declara la política de cada paso:

1. Input guardrails  → exception      (pendiente: lo cubre otro bloque del curso)
2. Cache lookup      → return on hit
3. Render prompt     → determinístico
4. LLM + Instructor  → fix · retry
5. Output guardrail  → filter
6. Cache write       → solo si es válido

`stream_estimate` recorre los mismos pasos, pero en el 4 entrega el resultado
parcial a medida que el modelo lo genera.
"""

from collections.abc import Iterator

import structlog
from pydantic import ValidationError

from app.cache.exact import ExactMatchCache
from app.config import settings
from app.guardrails.output import enforce_scope_response
from app.prompts.loader import render_estimation_prompt
from app.schemas import (
    EstimationRequest,
    EstimationResponse,
    EstimationResult,
    EstimationStreamEvent,
)
from app.services.llm_wrapper import generate_structured, stream_structured

logger = structlog.get_logger()

cache = ExactMatchCache(max_entries=settings.cache_max_entries)


def cache_key(request: EstimationRequest) -> str:
    # output_format no forma parte de la clave: solo cambia cómo dibuja la UI.
    return cache.make_key(
        settings.llm_model,
        settings.prompt_version,
        request.project_type.value,
        request.detail_level.value,
        request.description.strip(),
    )


def estimate(request: EstimationRequest) -> EstimationResponse:
    # 2. Cache lookup
    key = cache_key(request)
    cached = cache.get(key)
    if cached is not None:
        logger.info("estimation_completed", cache_hit=True)
        return _response(cached, cached=True)

    # 3. Render prompt
    system_prompt, user_message = render_estimation_prompt(
        request, version=settings.prompt_version
    )

    # 4. LLM + Instructor (fix · retry)
    result = generate_structured(system_prompt, user_message, EstimationResult)

    # 5. Output guardrail (filter)
    result = enforce_scope_response(result)

    # 6. Cache write: solo llega acá lo que pasó todas las validaciones
    cache.set(key, result)
    logger.info(
        "estimation_completed", cache_hit=False, out_of_scope=result.is_out_of_scope
    )
    return _response(result, cached=False)


def stream_estimate(request: EstimationRequest) -> Iterator[EstimationStreamEvent]:
    # 2. Cache lookup: un acierto se entrega directo como evento final
    key = cache_key(request)
    cached = cache.get(key)
    if cached is not None:
        logger.info("estimation_completed", cache_hit=True, streaming=True)
        yield _final_event(cached, cached=True)
        return

    # 3. Render prompt
    system_prompt, user_message = render_estimation_prompt(
        request, version=settings.prompt_version
    )

    # 4. LLM + Instructor en streaming parcial
    last_partial = None
    for partial in stream_structured(system_prompt, user_message, EstimationResult):
        last_partial = partial
        yield EstimationStreamEvent(
            type="partial", partial=partial.model_dump(mode="json", warnings=False)
        )

    # El parcial final se valida contra el schema completo. Si no pasa (por ejemplo,
    # los totales no cierran), se cae al camino fix · retry sin streaming.
    try:
        result = EstimationResult.model_validate(
            last_partial.model_dump(warnings=False) if last_partial else {}
        )
    except ValidationError:
        logger.warning("stream_result_invalid", fallback="generate_structured")
        result = generate_structured(system_prompt, user_message, EstimationResult)

    # 5. Output guardrail (filter)
    result = enforce_scope_response(result)

    # 6. Cache write
    cache.set(key, result)
    logger.info(
        "estimation_completed",
        cache_hit=False,
        streaming=True,
        out_of_scope=result.is_out_of_scope,
    )
    yield _final_event(result, cached=False)


def _final_event(result: EstimationResult, cached: bool) -> EstimationStreamEvent:
    return EstimationStreamEvent(type="final", response=_response(result, cached))


def _response(result: EstimationResult, cached: bool) -> EstimationResponse:
    return EstimationResponse(
        result=result, prompt_version=settings.prompt_version, cached=cached
    )
