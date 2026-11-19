"""Compresión híbrida del historial: resumen + anclas + ventana.

Lo crítico se queda literal (anclas), lo demás se resume y lo reciente sigue
vivo en la ventana.

`apply_compression` se llama justo después de `history.append()`. Si la ventana
no se desbordó, no hace nada. Si se desbordó:

1. Los turnos anclados que salen pasan a la lista de anclas (sin LLM).
2. Los demás se condensan en el resumen con UNA llamada al LLM, que rehace el
   resumen entero a partir del anterior (nada de resumen de resúmenes).

Política de fallo: si el summarizer falla, no se recorta. Los turnos quedan en
la ventana (que excede su límite por un turno) y se reintenta en el próximo.
Así no se pierde información, a cambio de un prompt un poco más largo.
"""

import structlog
from pydantic import BaseModel, Field

from app.config import settings
from app.prompts.loader import render_summary_prompt
from app.schemas import LLMCallMetrics
from app.services.llm_wrapper import (
    InvalidLLMOutputError,
    LLMProviderError,
    generate_structured,
)
from app.sessions import ConversationHistory

logger = structlog.get_logger()


class _ConversationSummary(BaseModel):
    summary: str = Field(description="El resumen completo y actualizado.")


def apply_compression(history: ConversationHistory) -> LLMCallMetrics | None:
    """Comprime el historial si la ventana se desbordó.

    Devuelve las métricas de la llamada al summarizer, o None si no hubo llamada.
    """
    if not history.needs_compression():
        return None

    to_summarize = history.turns_to_summarize()
    if not to_summarize:
        # Solo salieron anclas: se mueven sin llamar al LLM.
        history.compact()
        logger.info("history_compressed", summarized_turns=[], llm_call=False)
        return None

    system_prompt, user_message = render_summary_prompt(
        history.summary,
        to_summarize,
        max_chars=settings.summary_max_chars,
        version=settings.prompt_version,
    )
    try:
        call = generate_structured(
            system_prompt,
            user_message,
            _ConversationSummary,
            purpose="summary",
            model=settings.summary_model,
        )
    except (InvalidLLMOutputError, LLMProviderError) as error:
        logger.warning(
            "summary_failed",
            policy="keep_window",
            pending_turns=[turn.number for turn in to_summarize],
            error=str(error),
        )
        return None

    summarized = history.compact(call.result.summary.strip())
    logger.info(
        "history_compressed",
        summarized_turns=[turn.number for turn in summarized],
        summary_chars=len(history.summary),
        llm_call=True,
    )
    return call.metrics
