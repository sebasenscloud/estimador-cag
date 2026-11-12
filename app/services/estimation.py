"""Caso de uso: estimar un proyecto.

Orquesta el pipeline y declara la política de cada paso:

1. Input guardrails  → exception      (pendiente: lo cubre otro bloque del curso)
2. Cache lookup      → return on hit
3. Render prompt     → determinístico
4. LLM + Instructor  → fix · retry
5. Output guardrail  → filter
6. Cache write       → solo si es válido
"""

import structlog

from app.cache.exact import ExactMatchCache
from app.config import settings
from app.guardrails.output import enforce_scope_response
from app.prompts.loader import render_estimation_prompt
from app.schemas import EstimationRequest, EstimationResponse, EstimationResult
from app.services.llm_wrapper import generate_structured

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


def _response(result: EstimationResult, cached: bool) -> EstimationResponse:
    return EstimationResponse(
        result=result, prompt_version=settings.prompt_version, cached=cached
    )
