"""Guardrails sobre la salida del LLM.

Política FILTER: cuando el sistema no puede cumplir pero no es un error técnico,
se devuelve un EstimationResult con la forma de siempre y "Out of scope: ..."
al principio del summary. El cliente no necesita un caso de error especial.
"""

import structlog

from app.config import settings
from app.schemas import OUT_OF_SCOPE_PREFIX, EstimationResult

logger = structlog.get_logger()


def out_of_scope_reason(result: EstimationResult) -> str | None:
    """Devuelve el motivo por el que el resultado está fuera de scope, o None."""
    if result.is_out_of_scope:
        return "declarado por el modelo"
    if result.total_duration_weeks > settings.scope_max_weeks:
        return (
            f"el proyecto excede el alcance del estimador "
            f"(más de {settings.scope_max_weeks} semanas)"
        )
    return None


def enforce_scope_response(result: EstimationResult) -> EstimationResult:
    reason = out_of_scope_reason(result)
    if reason is None:
        return result

    logger.warning(
        "guardrail_triggered",
        guardrail="output_scope",
        policy="filter",
        reason=reason,
        confidence_pct=result.confidence_pct,
        total_duration_weeks=result.total_duration_weeks,
    )

    if result.is_out_of_scope:
        return result
    return result.model_copy(
        update={"summary": f"{OUT_OF_SCOPE_PREFIX} {reason}. {result.summary}"}
    )
