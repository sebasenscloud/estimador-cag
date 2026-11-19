"""Anclas: turnos que no salen del historial aunque la ventana avance.

Un turno se ancla cuando el mensaje del USUARIO contiene un compromiso duro
(un contrato firmado, un presupuesto tope, una fecha límite...). La respuesta
del assistant entra "por arrastre": se ancla el turno completo.

Criterio: mejor quedarse corto que anclar de más.

- Falso negativo (no se ancla algo importante): el turno sale de la ventana y
  termina en el resumen acumulativo. Se pierde detalle, pero no se rompe nada.
- Falso positivo (se ancla algo trivial): queda literal en el prompt para
  siempre. Eso es lo caro: tokens en cada llamada y ruido para el modelo.

Por eso hay pocas reglas y son frases específicas, no palabras sueltas.

Modos (ANCHOR_DETECTION_MODE):

- heuristic: las reglas de abajo, sin LLM. Gratis, instantáneo y auditable.
- llm: un clasificador LLM, para paráfrasis densas que las regex no atrapan.
  Agrega una llamada por turno.
"""

import re
from dataclasses import dataclass, field

import structlog
from pydantic import BaseModel, Field

from app.config import settings
from app.schemas import LLMCallMetrics
from app.services.llm_wrapper import (
    InvalidLLMOutputError,
    LLMProviderError,
    generate_structured,
)

logger = structlog.get_logger()


# Biblioteca curada de frases: verbos distintivos, términos legales o de
# compliance y compromisos explícitos. Las regex en inglés son las del curso;
# cada una suma su equivalente en español. La lista es corta a propósito:
# preferimos no anclar a anclar de más.
_HEURISTIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (rule, re.compile(pattern, re.IGNORECASE))
    for rule, pattern in (
        (
            "nda",
            (
                r"\b(nda|non[- ]?disclosure|under embargo|legal hold)\b"
                r"|\b(acuerdo de confidencialidad|bajo embargo)\b"
            ),
        ),
        (
            "signed_contract",
            (
                r"\b(signed|countersigned)\s+(the\s+)?(contract|sow|msa|agreement)\b"
                r"|\b(firmamos|firmaron|hemos firmado)\s+(el\s+)?(contrato|sow|msa|acuerdo)\b"
                r"|\bcontrato\s+(ya\s+)?firmado\b"
            ),
        ),
        (
            "scope_frozen",
            (
                r"\b(scope|backlog)\s+(is\s+)?(frozen|locked|final|fixed)\b"
                r"|\b(alcance|backlog)\s+(está\s+|quedó\s+)?(congelado|cerrado|bloqueado)\b"
            ),
        ),
        (
            "budget_locked",
            (
                r"\b(budget|cap|ceiling)\s+(is\s+)?(locked|fixed|approved|capped)\s+at\b"
                r"|\bpresupuesto\s+(máximo|tope)\s+(es\s+)?(de\s+)?[\d$€]"
                r"|\bpresupuesto\s+(está\s+|quedó\s+)?(fijado|topeado|aprobado)\s+en\b"
            ),
        ),
        ("compliance", r"\b(hipaa|gdpr|rgpd|sox|pci[- ]?dss|fda|iso[- ]?27001)\b"),
        (
            "deadline_hard",
            (
                r"\bhard\s+deadline\b|\bmust\s+go\s+live\s+by\b"
                r"|\bfecha\s+(límite|tope)\s+(innegociable|inamovible)\b"
                r"|\btiene\s+que\s+(salir|estar)\s+en\s+producción\s+(el|antes)\b"
            ),
        ),
        (
            "contractual",
            (
                r"\bcontractually\s+(bound|required|obliged)\b"
                r"|\b(obligatorio\s+por\s+contrato|obligación\s+contractual)\b"
            ),
        ),
        (
            "explicit_commitment",
            (
                r"\b(we|the (client|customer))\s+(agreed|committed)\s+to\b"
                r"|\b(acordamos|quedamos\s+en)\s+que\b"
                r"|\bel\s+cliente\s+se\s+comprometió\s+a\b"
            ),
        ),
    )
)


@dataclass
class AnchorMatch:
    """Resultado de revisar un mensaje. Lleva las reglas que dispararon para que
    el log estructurado pueda atribuir la decisión; útil cuando el detector
    promueve un turno que no se esperaba.

    En modo llm, `matched_rules` es ["llm"] y `reason` explica la decisión.
    """

    is_anchor: bool
    matched_rules: list[str] = field(default_factory=list)
    reason: str = ""


def detect_anchor_heuristic(text: str) -> AnchorMatch:
    matched = [rule for rule, pattern in _HEURISTIC_PATTERNS if pattern.search(text)]
    return AnchorMatch(is_anchor=bool(matched), matched_rules=matched)


class _AnchorClassification(BaseModel):
    """Schema Pydantic que usa el detector basado en LLM."""

    is_anchor: bool = Field(
        description=(
            "True si este turno del usuario introduce un compromiso duradero, una "
            "restricción legal o de compliance, un alcance congelado o un "
            "presupuesto fijo que la conversación no debe olvidar entre turnos."
        )
    )
    reason: str = Field(default="", max_length=200)


_LLM_SYSTEM_PROMPT = """Clasificás mensajes del usuario en una conversación sobre un proyecto de software.

Marcá is_anchor=true SOLO si el mensaje afirma un compromiso duro y vigente: un
contrato o acuerdo firmado, confidencialidad, una normativa que aplica, un
alcance cerrado, un presupuesto con tope, una fecha límite innegociable o un
compromiso explícito del cliente o del equipo.

Ante la duda, is_anchor=false: es mejor no anclar que anclar algo trivial. Una
pregunta, una hipótesis o una preferencia no son compromisos.

En reason explicá en una frase corta qué compromiso detectaste (o por qué no)."""


def detect_anchor(text: str) -> tuple[AnchorMatch, LLMCallMetrics | None]:
    """Detecta si el mensaje del usuario ancla el turno, según el modo configurado.

    Devuelve (AnchorMatch, métricas de la llamada si el modo es llm).
    """
    if settings.anchor_detection_mode != "llm":
        return detect_anchor_heuristic(text), None

    try:
        call = generate_structured(
            _LLM_SYSTEM_PROMPT,
            text,
            _AnchorClassification,
            purpose="anchor_detection",
        )
    except (InvalidLLMOutputError, LLMProviderError) as error:
        # Si el clasificador falla, se cae a las reglas: nunca se pierde el turno.
        logger.warning(
            "anchor_detection_failed", fallback="heuristic", error=str(error)
        )
        return detect_anchor_heuristic(text), None

    classification = call.result
    match = AnchorMatch(
        is_anchor=classification.is_anchor,
        matched_rules=["llm"] if classification.is_anchor else [],
        reason=classification.reason,
    )
    return match, call.metrics
