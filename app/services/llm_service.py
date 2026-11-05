import time
from collections.abc import Iterator
from textwrap import dedent

import structlog

from app.config import settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.schemas import (
    PROJECT_TYPE_LABELS,
    DetailLevel,
    EstimationRequest,
    EstimationResponse,
    OutputFormat,
)
from app.services.cache import ExactMatchCache
from app.services.providers import LLMProvider, LLMResult, get_provider

logger = structlog.get_logger()

cache = ExactMatchCache(max_entries=settings.cache_max_entries)

# Subir esta versión cada vez que cambie el texto del prompt.
PROMPT_VERSION = "v2"

SYSTEM_INSTRUCTIONS = (
    "Sos un estimador experto de proyectos de software. Tu trabajo es leer "
    "la descripción de un proyecto y generar una estimación de esfuerzo en horas.\n\n"
    "Usá las estimaciones previas que aparecen abajo como referencia para mantener "
    "un criterio de horas coherente. Respetá el tipo de proyecto, el nivel de "
    "detalle y el formato de salida que se piden en cada solicitud. "
    "Si la descripción no tiene información suficiente para estimar alguna "
    "parte, indicalo y explicá qué supuestos tomaste."
)

DETAIL_LEVEL_INSTRUCTIONS = {
    DetailLevel.SUMMARY: (
        "Resumido: solo los grandes bloques de trabajo, el total de horas, "
        "el equipo y la duración."
    ),
    DetailLevel.MEDIUM: (
        "Intermedio: las tareas principales con sus horas, el total, "
        "el equipo y la duración."
    ),
    DetailLevel.DETAILED: (
        "Detallado: un desglose fino de tareas y subtareas con horas, "
        "riesgos, supuestos, total, equipo y duración."
    ),
}

OUTPUT_FORMAT_INSTRUCTIONS = {
    OutputFormat.PHASES_TABLE: (
        "Una tabla en Markdown organizada por fases del proyecto, con las columnas "
        "Fase, Tareas y Horas, y una fila final con el total."
    ),
    OutputFormat.LINE_ITEMS: (
        "Una lista de ítems en Markdown, un ítem por tarea con sus horas, "
        "y el total al final."
    ),
    OutputFormat.NARRATIVE: (
        "Texto narrativo en párrafos, explicando el razonamiento de la estimación "
        "e incluyendo las horas por bloque y el total."
    ),
}


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


def build_user_message(request: EstimationRequest) -> str:
    return (
        f"<tipo_de_proyecto>{PROJECT_TYPE_LABELS[request.project_type]}"
        "</tipo_de_proyecto>\n"
        f"<nivel_de_detalle>{DETAIL_LEVEL_INSTRUCTIONS[request.detail_level]}"
        "</nivel_de_detalle>\n"
        f"<formato_de_salida>{OUTPUT_FORMAT_INSTRUCTIONS[request.output_format]}"
        "</formato_de_salida>\n\n"
        f"<descripcion>\n{request.description.strip()}\n</descripcion>"
    )


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
        prompt_version=PROMPT_VERSION,
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
        prompt_version=PROMPT_VERSION,
        streaming=streaming,
        error_type=type(error).__name__,
        error=str(error),
    )


def generate_estimation(request: EstimationRequest) -> EstimationResponse:
    start = time.perf_counter()
    provider = get_provider()
    system_prompt = build_system_prompt()
    user_message = build_user_message(request)
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
    return EstimationResponse(text=result.text, prompt_version=PROMPT_VERSION)


def stream_estimation(
    request: EstimationRequest, metrics: dict | None = None
) -> Iterator[str]:
    start = time.perf_counter()
    provider = get_provider()
    system_prompt = build_system_prompt()
    user_message = build_user_message(request)
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
            prompt_version=PROMPT_VERSION,
        )
