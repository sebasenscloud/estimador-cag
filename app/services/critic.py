"""Crítico del Actor-Critic-Boss: audita una estimación, no la reescribe.

Función sin estado: (mensaje, metadata, tier, estimación) → CriticFeedback.
No toca la sesión ni decide qué hacer con su salida: eso es trabajo del Boss.
Separar responsabilidades es lo que hace auditable al patrón.

Fallback elegante: si el crítico falla (proveedor caído, salida inválida),
devuelve `accept` con confidence_in_review=0. El usuario recibe el borrador
del actor y el log (critic_failed_fallback_accept) delata que fue degradado.

Basado en app/services/critic.py del curso (rama session_05_live).
"""

import structlog

from app.config import settings
from app.prompts.loader import render_critic_prompt
from app.schemas import EstimationResult, LLMCallMetrics
from app.schemas.critic import CriticFeedback
from app.services.llm_wrapper import (
    InvalidLLMOutputError,
    LLMProviderError,
    generate_structured,
)
from app.sessions import ProjectMetadata

logger = structlog.get_logger()

PROMPT_VERSION = "v1"


def review(
    transcript: str,
    metadata: ProjectMetadata,
    tier: str,
    result: EstimationResult,
    detail_level: str,
) -> tuple[CriticFeedback, LLMCallMetrics | None]:
    system_prompt, user_message = render_critic_prompt(
        transcript, metadata, tier, result, detail_level, version=PROMPT_VERSION
    )
    try:
        call = generate_structured(
            system_prompt,
            user_message,
            CriticFeedback,
            purpose="critic",
            model=settings.critic_model or None,
        )
    except (InvalidLLMOutputError, LLMProviderError) as error:
        logger.warning(
            "critic_failed_fallback_accept",
            error_type=type(error).__name__,
            error=str(error)[:200],
        )
        return CriticFeedback(verdict="accept", confidence_in_review=0), None

    feedback = call.result
    logger.info(
        "critic_completed",
        verdict=feedback.verdict,
        issue_count=len(feedback.issues),
        confidence=feedback.confidence_in_review,
    )
    return feedback, call.metrics
